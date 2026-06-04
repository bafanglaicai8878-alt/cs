"""在线支付：GoPay / 易支付 V1（MD5）+ USDT 手动收款。"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from extensions.notification import send_message
from extensions.store import load_extensions, save_extensions

_log = logging.getLogger(__name__)

DEFAULT_GATEWAY = "https://pay.maihao.la"
DEFAULT_PLATFORM_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtfXEJpLUVE9A1NCvX72+
jHqmvmlAZsilVcBgSg+UE0uCAKWhy2TzqiZ1OOXGvkZEwxnLLXSgVoK3dWYGqo9o
qNy6QXpUeu1Av1YqyfifK0upbuVOl9aZr9pV2qf1VAAkHuysNoNwMEPPzd8p0d/2
0nhi8AO8AirnRwM6/xZUuByzERZldMnjSaCYvd5jxpZ2gIRO6b5M/yDE0e7K85oM
VkzFD4mJN861bFYYeKMg7DArQ0cqNO4P4EUC+msudLC2+RT5RsC4SBU6soADpifM
U4ym0d88qaGjJ27oLONZTnFJYSRzuj3qwjPFmsluj93dP8GxnHxF3aUgR7ppaXw9
oQIDAQAB
-----END PUBLIC KEY-----"""


def get_payment_settings() -> Dict[str, Any]:
    return load_extensions().get("payment", {}).get("settings", {})


def get_public_payment_status() -> Dict[str, Any]:
    s = get_payment_settings()
    channels: List[str] = []
    if s.get("epay_enabled") and s.get("epay_pid") and s.get("epay_md5_key"):
        channels.append("alipay")
    if s.get("usdt_enabled") and s.get("usdt_address"):
        channels.append("usdt")
    return {
        "online_pay": bool(channels),
        "epay_enabled": bool(s.get("epay_enabled")),
        "usdt_enabled": bool(s.get("usdt_enabled")),
        "channels": channels,
    }


def update_payment_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    settings = data.setdefault("payment", {}).setdefault("settings", {})
    settings.update(patch)
    save_extensions(data)
    return settings


def _sign_params(params: Dict[str, Any], md5_key: str) -> str:
    items = []
    for k in sorted(params.keys()):
        if k in ("sign", "sign_type"):
            continue
        v = params[k]
        if v is None or v == "":
            continue
        items.append(f"{k}={v}")
    raw = "&".join(items) + md5_key
    return hashlib.md5(raw.encode("utf-8")).hexdigest().lower()


def _build_sign_string(params: Dict[str, Any]) -> str:
    items = []
    for k in sorted(params.keys()):
        if k in ("sign", "sign_type"):
            continue
        v = params[k]
        if v is None or v == "":
            continue
        items.append(f"{k}={v}")
    return "&".join(items)


def verify_epay_sign(params: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> bool:
    settings = settings or get_payment_settings()
    sign = str(params.get("sign", "") or "")
    if not sign:
        return False
    sign_type = str(params.get("sign_type", "MD5") or "MD5").upper()
    if sign_type == "MD5":
        md5_key = str(settings.get("epay_md5_key", "") or "")
        if not md5_key:
            return False
        expected = _sign_params(params, md5_key)
        return secrets.compare_digest(expected, sign.lower())
    if sign_type == "RSA":
        pub = str(settings.get("epay_platform_public_key", "") or DEFAULT_PLATFORM_PUBLIC_KEY).strip()
        if not pub:
            return False
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            data = _build_sign_string(params).encode("utf-8")
            public_key = serialization.load_pem_public_key(pub.encode("utf-8"), backend=default_backend())
            public_key.verify(
                base64.b64decode(sign),
                data,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            _log.warning("RSA 验签失败: %s", e)
            return False
    return False


def _epay_code_ok(result: Dict[str, Any]) -> bool:
    msg = str(result.get("msg") or result.get("message") or "").strip().lower()
    if _peek_pay_url(result) and msg in ("success", "ok", ""):
        return True
    code = result.get("code", result.get("status"))
    if code is True:
        return True
    try:
        return int(code) in (0, 1)
    except (TypeError, ValueError):
        return str(code).strip().lower() in ("0", "1", "success", "ok")


def _peek_pay_url(result: Dict[str, Any]) -> str:
    for key in ("pay_info", "payurl", "qrcode", "url", "code_url", "pay_jump_url"):
        url = str(result.get(key) or "").strip()
        if url.startswith(("http://", "https://", "alipays:", "weixin:")):
            return url
    nested = result.get("data")
    if isinstance(nested, dict):
        for key in ("pay_info", "payurl", "qrcode", "url", "code_url", "pay_jump_url"):
            url = str(nested.get(key) or "").strip()
            if url.startswith(("http://", "https://", "alipays:", "weixin:")):
                return url
    return ""


def _build_submit_url(gateway: str, params: Dict[str, Any]) -> str:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"{gateway}/api/pay/submit?{qs}"


def _extract_pay_url(result: Dict[str, Any], gateway: str, params: Dict[str, Any]) -> str:
    found = _peek_pay_url(result)
    if found:
        return found
    candidates: list = []
    for key in ("pay_info", "payurl", "qrcode", "url", "code_url", "pay_jump_url"):
        candidates.append(result.get(key))
    nested = result.get("data")
    if isinstance(nested, dict):
        for key in ("pay_info", "payurl", "qrcode", "url", "code_url", "pay_jump_url"):
            candidates.append(nested.get(key))
    for raw in candidates:
        url = str(raw or "").strip()
        if not url:
            continue
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("alipays:") or url.startswith("weixin:"):
            return url
        if url.startswith("/"):
            return f"{gateway.rstrip('/')}{url}"
    return _build_submit_url(gateway, params)


def _resolve_checkout_url(order: Dict[str, Any], settings: Dict[str, Any], gateway: str) -> str:
    mode = str(settings.get("epay_checkout_mode") or "cashier").strip().lower()
    submit = str(order.get("pay_submit_url") or "").strip()
    payurl = str(order.get("payurl") or "").strip()
    qrcode = str(order.get("qrcode") or "").strip()
    host = gateway.replace("https://", "").replace("http://", "").split("/")[0].lower()

    if mode == "cashier":
        if submit:
            return submit
        if payurl and host and host in payurl.lower():
            return payurl
        if qrcode.startswith("http"):
            return qrcode
        return payurl or submit

    if payurl:
        return payurl
    if qrcode.startswith("http") or qrcode.startswith("alipays:"):
        return qrcode
    return submit


def get_order(order_id: str, user_id: str = "") -> Dict[str, Any]:
    order = load_extensions().get("payment", {}).get("orders", {}).get(order_id)
    if not order:
        raise ValueError("订单不存在")
    if user_id and order.get("user_id") != user_id:
        raise ValueError("无权查看该订单")
    public = {
        "id": order.get("id"),
        "amount_cny": order.get("amount_cny"),
        "channel": order.get("channel"),
        "status": order.get("status"),
        "created_at": order.get("created_at"),
        "paid_at": order.get("paid_at"),
        "trade_no": order.get("trade_no"),
        "checkout_url": order.get("checkout_url") or order.get("pay_submit_url") or order.get("payurl"),
    }
    if order.get("qrcode") and order.get("channel") == "alipay":
        public["qrcode"] = order.get("qrcode")
    return public


def _gateway_base(settings: Dict[str, Any]) -> str:
    return str(settings.get("epay_gateway") or DEFAULT_GATEWAY).rstrip("/")


def _epay_create_request(api_url: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    with httpx.Client(timeout=20, verify=True) as client:
        resp = client.post(api_url, data=params)
        resp.raise_for_status()
        raw_text = resp.text.strip()
    try:
        result = resp.json()
    except Exception:
        result = {"code": -1, "msg": f"支付网关返回异常：{raw_text[:200]}"}
    if not isinstance(result, dict):
        result = {"code": -1, "msg": "支付网关返回格式错误"}
    return result, raw_text


def notify_url(site_base: str) -> str:
    return f"{site_base.rstrip('/')}/api/payment/epay/notify"


def return_url(site_base: str) -> str:
    return f"{site_base.rstrip('/')}/api/payment/epay/return"


def epay_configured(settings: Optional[Dict[str, Any]] = None) -> bool:
    s = settings or get_payment_settings()
    return bool(s.get("epay_enabled") and s.get("epay_pid") and s.get("epay_md5_key"))


def test_epay_connection() -> Dict[str, Any]:
    s = get_payment_settings()
    if not epay_configured(s):
        raise ValueError("请先填写并启用 GoPay 商户号与 MD5 密钥")
    gateway = _gateway_base(s)
    pid = str(s.get("epay_pid", "")).strip()
    key = str(s.get("epay_md5_key", "")).strip()
    url = f"{gateway}/api.php?act=query&pid={urllib.parse.quote(pid)}&key={urllib.parse.quote(key)}"
    with httpx.Client(timeout=15, verify=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            raise ValueError(f"网关返回非 JSON：{resp.text[:200]}")
    if str(data.get("code", "")) not in ("1", "200") and data.get("status") not in (1, "1", True):
        raise ValueError(str(data.get("msg") or data.get("message") or "查询失败"))
    return {"ok": True, "gateway": gateway, "pid": pid, "merchant": data}


def create_order(
    user_id: str,
    username: str,
    amount: float,
    channel: str,
    *,
    site_base: str = "",
    client_ip: str = "",
    pay_type: str = "",
) -> Dict[str, Any]:
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("金额必须大于 0")
    s = get_payment_settings()
    channel = channel.lower()
    if channel == "alipay":
        if not epay_configured(s):
            raise ValueError("GoPay 在线支付未配置或未启用")
    elif channel == "usdt":
        if not s.get("usdt_enabled"):
            raise ValueError("USDT 未启用")
        if not s.get("usdt_address"):
            raise ValueError("请先配置 USDT 收款地址")
    else:
        raise ValueError("不支持的支付渠道")

    order_id = secrets.token_hex(8).upper()
    data = load_extensions()
    orders = data.setdefault("payment", {}).setdefault("orders", {})
    order = {
        "id": order_id,
        "user_id": user_id,
        "username": username,
        "amount_cny": amount,
        "channel": channel,
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "paid_at": None,
        "tx_id": "",
        "trade_no": "",
    }
    if channel == "usdt":
        rate = float(s.get("usdt_rate") or 7.2)
        order["amount_usdt"] = round(amount / rate, 4)
        order["usdt_address"] = str(s.get("usdt_address", ""))
        orders[order_id] = order
        save_extensions(data)
        return order

    if not site_base:
        raise ValueError("缺少站点地址，无法生成回调 URL")
    pay_type = (pay_type or s.get("epay_pay_type") or "alipay").strip() or "alipay"
    checkout_mode = str(s.get("epay_checkout_mode") or "cashier").strip().lower()
    gateway = _gateway_base(s)
    params: Dict[str, Any] = {
        "pid": str(s.get("epay_pid", "")).strip(),
        "type": pay_type,
        "out_trade_no": order_id,
        "name": f"账户充值-{username}"[:127],
        "money": f"{amount:.2f}",
        "notify_url": notify_url(site_base),
        "return_url": return_url(site_base),
        "param": user_id,
        "timestamp": str(int(time.time())),
        "method": "web" if checkout_mode == "cashier" else "jump",
    }
    if client_ip:
        params["clientip"] = client_ip
    params["sign_type"] = "MD5"
    params["sign"] = _sign_params(params, str(s.get("epay_md5_key", "")))
    order["pay_submit_url"] = _build_submit_url(gateway, params)

    api_url = f"{gateway}/api/pay/create"
    result, raw_text = _epay_create_request(api_url, params)
    if not _epay_code_ok(result):
        fallback_type = "alipay"
        if pay_type != fallback_type:
            _log.warning("GoPay type=%s 下单失败，尝试回退 %s: %s", pay_type, fallback_type, raw_text[:300])
            params["type"] = fallback_type
            params.pop("sign", None)
            params.pop("sign_type", None)
            params["sign_type"] = "MD5"
            params["sign"] = _sign_params(params, str(s.get("epay_md5_key", "")))
            order["pay_submit_url"] = _build_submit_url(gateway, params)
            result, raw_text = _epay_create_request(api_url, params)
    if not _epay_code_ok(result):
        msg = str(result.get("msg") or result.get("message") or "创建支付订单失败")
        hint = ""
        if "创建订单失败" in msg or int(result.get("code", -999) or -999) < 0:
            hint = f"（当前 type={pay_type}，请检查 GoPay 后台是否已开通该支付产品，或改为 alipay）"
        _log.warning("GoPay 下单失败: %s", raw_text[:500])
        raise ValueError(msg + hint)

    resp_sign = str(result.get("sign", "") or "")
    if resp_sign:
        verify_payload = {k: v for k, v in result.items() if k not in ("sign", "sign_type") and v not in (None, "")}
        expected = _sign_params(verify_payload, str(s.get("epay_md5_key", "")))
        if not secrets.compare_digest(expected, resp_sign.lower()):
            _log.warning("GoPay 回包 MD5 验签未通过，仍继续下单")

    pay_url = _extract_pay_url(result, gateway, params)
    order["trade_no"] = str(result.get("trade_no") or (result.get("data") or {}).get("trade_no") or "")
    order["payurl"] = pay_url
    order["qrcode"] = str(result.get("qrcode") or (result.get("data") or {}).get("qrcode") or "")
    order["checkout_url"] = _resolve_checkout_url(order, s, gateway)
    orders[order_id] = order
    save_extensions(data)
    return order


def complete_order(order_id: str, tx_id: str = "", operator: str = "system", trade_no: str = "") -> Dict[str, Any]:
    data = load_extensions()
    orders = data.get("payment", {}).get("orders", {})
    order = orders.get(order_id)
    if not order:
        raise ValueError("订单不存在")
    if order.get("status") == "paid":
        return order
    order["status"] = "paid"
    order["paid_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if tx_id:
        order["tx_id"] = tx_id
    if trade_no:
        order["trade_no"] = trade_no
    order["operator"] = operator
    orders[order_id] = order
    save_extensions(data)
    return order


def credit_user_balance(admin_service, order: Dict[str, Any]) -> None:
    uid = order.get("user_id")
    amount = float(order.get("amount_cny", 0))
    with admin_service._write_lock():
        raw = admin_service.get_raw_user(uid)
        if raw:
            raw["balance"] = round(float(raw.get("balance", 0)) + amount, 4)


def handle_epay_notify(params: Dict[str, Any], admin_service) -> Tuple[bool, str]:
    s = get_payment_settings()
    if not verify_epay_sign(params, s):
        return False, "sign error"

    trade_status = str(params.get("trade_status", "") or "")
    if trade_status != "TRADE_SUCCESS":
        return True, "ignore"

    out_trade_no = str(params.get("out_trade_no", "") or "").strip()
    if not out_trade_no:
        return False, "missing out_trade_no"

    try:
        money = round(float(params.get("money", 0)), 2)
    except (TypeError, ValueError):
        return False, "invalid money"

    order = complete_order(
        out_trade_no,
        tx_id=str(params.get("api_trade_no") or params.get("trade_no") or ""),
        operator="epay",
        trade_no=str(params.get("trade_no") or ""),
    )
    stored = round(float(order.get("amount_cny", 0)), 2)
    if abs(stored - money) > 0.01:
        _log.warning("订单 %s 金额不一致：本地 %.2f 通知 %.2f", out_trade_no, stored, money)

    credit_user_balance(admin_service, order)
    username = order.get("username", "")
    send_message("充值到账", f"代理 {username} 在线支付 ¥{stored:.2f} 已自动到账（订单 {out_trade_no}）")
    return True, "success"


def verify_callback_signature(payload: Dict[str, Any], signature: str) -> bool:
    s = get_payment_settings()
    secret = str(s.get("callback_secret", ""))
    if not secret:
        return False
    raw = f"{payload.get('order_id','')}|{payload.get('amount','')}|{secret}"
    expected = hashlib.sha256(raw.encode()).hexdigest()
    return secrets.compare_digest(expected, signature or "")


def list_orders(user_id: str = "", limit: int = 50) -> list:
    orders = list(load_extensions().get("payment", {}).get("orders", {}).values())
    orders.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if user_id:
        orders = [o for o in orders if o.get("user_id") == user_id]
    return orders[:limit]
