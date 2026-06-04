"""扩展功能 HTTP 路由（供 web_server 调用）。"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, Optional, TYPE_CHECKING

from extensions import agent_extras, api_keys, cdk_extras, game_ops, help_tutorial, i18n, notification, payment, security, stats, sync_progress, tenants
from extensions.store import load_extensions, save_extensions

if TYPE_CHECKING:
    from web_server import WebHandler

_log = logging.getLogger(__name__)
SERVER = None  # injected by web_server


def log_activation(cdk: str, appid: str, machine: str, ok: bool) -> None:
    from datetime import datetime

    data = load_extensions()
    logs = data.setdefault("activation_logs", [])
    logs.append({
        "cdk": cdk[:4] + "****",
        "appid": appid,
        "machine": machine[:32],
        "ok": ok,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    if len(logs) > 5000:
        logs[:] = logs[-5000:]
    save_extensions(data)


async def sync_catalogs_tracked(server) -> Dict[str, Any]:
    sync_progress.start_sync()
    try:
        sync_progress.update_sync(15, "拉取 GitHub 清单源…")
        result = await server.sync_catalogs()
        sync_progress.update_sync(90, "写入数据库…")
        sync_progress.finish_sync(True, f"可入库 {result.get('manifest', {}).get('total', 0)} 款")
        return result
    except Exception as e:
        sync_progress.finish_sync(False, str(e))
        raise


def _extract_api_key_raw(handler: "WebHandler", payload: Optional[Dict[str, Any]] = None) -> str:
    if payload:
        body_key = str(payload.get("api_key", "") or "").strip()
        if body_key:
            return body_key
    auth = str(handler.headers.get("Authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    token = handler._get_token()
    if token and (token.startswith("csk_") or len(token) >= 16):
        return token
    return ""


def _api_key_from_query(qs: dict) -> str:
    return str((qs.get("api_key") or [""])[0] or "").strip()


def _verify_api_or_auth(
    handler: "WebHandler",
    scopes: Optional[list] = None,
    *,
    payload: Optional[Dict[str, Any]] = None,
    qs: Optional[dict] = None,
):
    from extensions.api_keys import touch_api_key, verify_api_key

    key_raw = _extract_api_key_raw(handler, payload)
    if not key_raw and qs is not None:
        key_raw = _api_key_from_query(qs)
    entry = verify_api_key(key_raw) if key_raw else None
    if entry:
        if scopes and not any(s in (entry.get("scopes") or []) for s in scopes):
            handler._send_json(403, {"ok": False, "message": "API Key 权限不足"})
            return None, None
        user = SERVER.admin.get_user(str(entry.get("owner_id", "")))
        if not user or not user.enabled:
            handler._send_json(403, {"ok": False, "message": "代理账号无效或已禁用"})
            return None, None
        if user.role != "agent":
            handler._send_json(403, {"ok": False, "message": "仅代理 API Key 可调用"})
            return None, None
        SERVER.admin._refresh()
        user = SERVER.admin.get_user(str(entry.get("owner_id", "")))
        if not user or not user.enabled:
            handler._send_json(403, {"ok": False, "message": "代理账号无效或已禁用"})
            return None, None
        touch_api_key(str(entry.get("id", "")))
        return user, entry

    user = handler._require_auth()
    if not user:
        return None, None
    return user, None


def _api_base(handler: "WebHandler") -> str:
    return handler._api_base_for_request().rstrip("/")


def _billing_mode_from_payload(payload: Dict[str, Any]) -> str:
    if "billing_mode" in payload:
        mode = str(payload.get("billing_mode", "immediate")).strip()
        return mode if mode in ("immediate", "on_activate") else "immediate"
    try:
        gen_mode = int(payload.get("generation_mode", 0))
    except (TypeError, ValueError):
        gen_mode = 0
    return "on_activate" if gen_mode == 1 else "immediate"


def _agent_api_key_payload(handler: "WebHandler", user, *, api_key: Optional[str] = None) -> Dict[str, Any]:
    from extensions.api_keys import build_api_docs, get_agent_key_info

    base = _api_base(handler)
    info = get_agent_key_info(user.id)
    current_key = api_key or (info.get("api_key") if info else "") or SERVER.admin.get_agent_api_key(user.id)
    sample = current_key or "你的API密钥"
    endpoint = f"{base}/api2/cdkeys/generate"
    payload: Dict[str, Any] = {
        "ok": True,
        "api_base": base,
        "api_key": current_key or None,
        "has_key": bool(current_key),
        "has_api_key": bool(current_key),
        "generate_url": endpoint,
        "endpoints": {
            "generate": endpoint,
            "list": f"{base}/api/v1/cdk/list",
            "status": f"{base}/api/v1/cdk/status",
            "games": f"{base}/api/v1/games",
        },
        "examples": build_api_docs(base, sample),
    }
    if info:
        payload["api_key_created_at"] = info.get("created_at", "")
    return payload


def _with_generate_compat_fields(result: Dict[str, Any]) -> Dict[str, Any]:
    ok = bool(result.get("ok"))
    message = str(result.get("message", "") or ("success" if ok else "failed"))
    cdks = result.get("cdks") if isinstance(result.get("cdks"), list) else []
    first_cdk = str(cdks[0]) if cdks else ""
    quantity = int(result.get("quantity", len(cdks)) or 0)
    data = {
        "appid": str(result.get("appid", "") or ""),
        "name": str(result.get("name", "") or ""),
        "quantity": quantity,
        "cdks": cdks,
        "cards": cdks,
        "cdk": first_cdk,
        "card": first_cdk,
    }
    # 兼容第三方对返回字段名的差异解析（如 code/msg/data/status/success）
    return {
        **result,
        "success": ok,
        "status": 1 if ok else 0,
        "code": 200 if ok else 400,
        "msg": message,
        "data": data,
    }


def _handle_api2_generate_steamox(handler: "WebHandler", payload: Dict[str, Any]) -> None:
    """兼容 steamox/爱奇索风格返回格式。"""
    from web_server import run_async

    raw_key = _extract_api_key_raw(handler, payload)
    entry = api_keys.verify_api_key(raw_key) if raw_key else None
    if not entry:
        handler._send_json(401, {"error": "Invalid API key"})
        return
    user = SERVER.admin.get_user(str(entry.get("owner_id", "")))
    if not user or not user.enabled or user.role != "agent":
        handler._send_json(401, {"error": "Invalid API key"})
        return

    appid = str(payload.get("appid", "")).strip()
    try:
        quantity = int(payload.get("quantity", payload.get("count", 1)))
    except (TypeError, ValueError):
        quantity = 1
    notes = str(payload.get("notes", "") or payload.get("note", "API")).strip() or "API"

    raw_mode = payload.get("generation_mode", payload.get("billing_mode", 0))
    try:
        mode_int = int(raw_mode)
    except (TypeError, ValueError):
        mode_int = -1
    if mode_int not in (0, 1):
        handler._send_json(
            400,
            {
                "error": (
                    "Invalid request parameters: Key: "
                    "'API2GenerateCDKeyRequest.GenerationMode' Error:Field validation for "
                    "'GenerationMode' failed on the 'max' tag"
                )
            },
        )
        return
    billing_mode = "on_activate" if mode_int == 1 else "immediate"

    ok_import, import_msg = run_async(SERVER.service.check_importable(appid, deep_probe=True))
    if not ok_import:
        handler._send_json(400, {"error": import_msg})
        return

    result = run_async(
        SERVER.generate_cdks(
            appid,
            str(payload.get("name", "") or payload.get("game_name", "")).strip(),
            max(1, min(quantity, 100)),
            notes,
            user=user,
            billing_mode=billing_mode,
            expire_days=int(payload.get("expire_days", 0) or 0),
        )
    )
    if not result.get("ok"):
        handler._send_json(400, {"error": str(result.get("message", "Generate failed"))})
        return
    handler._send_json(200, {"status": "success", "generated_keys": result.get("cdks") or []})


def _handle_cdk_generate_api(handler: "WebHandler", payload: Dict[str, Any], user) -> None:
    from platform_utils import RATE_LIMITER
    from web_server import run_async

    appid = str(payload.get("appid", "")).strip()
    name = str(payload.get("name", "") or payload.get("game_name", "")).strip()
    try:
        count = int(payload.get("quantity", payload.get("count", 1)))
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 100))
    note = str(payload.get("notes", "") or payload.get("note", "API")).strip() or "API"
    billing_mode = _billing_mode_from_payload(payload)
    expire_days = int(payload.get("expire_days", 0) or 0)
    if not appid.isdigit():
        handler._send_json(400, {"ok": False, "message": "AppID 无效"})
        return
    ok, msg = RATE_LIMITER.allow(f"api-gen:{user.id}", 60, 60)
    if not ok:
        handler._send_json(429, {"ok": False, "message": msg})
        return
    result = run_async(
        SERVER.generate_cdks(
            appid,
            name,
            count,
            note,
            user=user,
            billing_mode=billing_mode,
            expire_days=expire_days,
        )
    )
    if result.get("ok"):
        cdks = result.get("cdks") or []
        result["quantity"] = len(cdks)
        result["generation_mode"] = 0 if billing_mode == "immediate" else 1
        if cdks:
            base = handler._irm_cmd_base()
            result["install_cmd"] = f"irm {base} | iex"
            result["cdk_cmd"] = f'$cdk="{cdks[0]}"; irm {base} | iex'
    result = _with_generate_compat_fields(result)
    code = 200 if result.get("ok") else 400
    handler._send_json(code, result)


def handle_get(handler: "WebHandler", path: str, qs: dict) -> bool:
    if path == "/health":
        return False  # let main handler enhance

    if path in ("/extensions", "/extensions.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "extensions.html")
        return True

    if path in ("/user", "/user-center", "/user-center.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "user-center.html")
        return True

    if path in ("/shop", "/mall", "/shop.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "shop.html")
        return True

    if path in ("/pay", "/cashier", "/pay.html", "/cashier.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "cashier.html")
        return True

    if path in ("/help", "/help.html", "/tutorial", "/tutorial.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "help.html")
        return True

    if path == "/api/public/help-tutorial":
        handler._send_json(200, {"ok": True, "tutorial": help_tutorial.get_public_help_tutorial()})
        return True

    if path == "/api/admin/help-tutorial":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        handler._send_json(200, {"ok": True, "tutorial": help_tutorial.get_help_tutorial()})
        return True

    if path in ("/web-box", "/web-box.html"):
        from web_server import ROOT
        handler._send_html_file(ROOT / "static" / "web-box.html")
        return True

    if path == "/api/i18n":
        lang = (qs.get("lang") or ["zh"])[0]
        handler._send_json(200, {"ok": True, "lang": lang, "strings": i18n.get_all(lang)})
        return True

    if path == "/api/public/cdk/lookup":
        cdk = (qs.get("cdk") or [""])[0].strip()
        machine = (qs.get("machine") or [""])[0].strip()
        if not cdk:
            handler._send_json(400, {"ok": False, "message": "请提供 CDK"})
            return True
        raw = SERVER.cdk.get_key_raw(cdk)
        if not raw:
            handler._send_json(404, {"ok": False, "message": "CDK 不存在"})
            return True
        handler._send_json(200, {
            "ok": True,
            "appid": raw.get("appid"),
            "name": raw.get("name"),
            "used": bool(raw.get("used")),
            "used_at": raw.get("used_at"),
            "used_machine": raw.get("used_machine") if not machine or raw.get("used_machine") == machine else "(其他设备)",
            "expires_at": raw.get("expires_at"),
            "revoked": bool(raw.get("revoked")),
        })
        return True

    if path == "/api/public/mall/packages":
        pkgs = [p for p in cdk_extras.list_packages() if p.get("enabled")]
        handler._send_json(200, {"ok": True, "packages": pkgs, "settings": cdk_extras.get_mall_settings()})
        return True

    if path == "/api/public/payment/status":
        handler._send_json(200, {"ok": True, **payment.get_public_payment_status()})
        return True

    if path == "/api/payment/epay/notify":
        params = {k: (v[0] if isinstance(v, list) else v) for k, v in qs.items()}
        ok, msg = payment.handle_epay_notify(params, SERVER.admin)
        body = msg.encode("utf-8")
        handler.send_response(200 if ok else 403)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True

    if path == "/api/payment/epay/return":
        params = {k: (v[0] if isinstance(v, list) else v) for k, v in qs.items()}
        ok = payment.verify_epay_sign(params)
        trade_status = str(params.get("trade_status", "") or "")
        out_no = str(params.get("out_trade_no", "") or "")
        if ok and trade_status == "TRADE_SUCCESS" and out_no:
            try:
                data = load_extensions()
                order = data.get("payment", {}).get("orders", {}).get(out_no)
                if order and order.get("status") != "paid":
                    payment.complete_order(out_no, trade_no=str(params.get("trade_no") or ""), operator="epay-return")
                    payment.credit_user_balance(SERVER.admin, order)
            except Exception:
                _log.exception("同步跳转补单失败")
        loc = f"/portal?page=recharge&paid={'1' if ok and trade_status == 'TRADE_SUCCESS' else '0'}&order={urllib.parse.quote(out_no)}"
        handler.send_response(302)
        handler.send_header("Location", loc)
        handler.end_headers()
        return True

    if path == "/api/payment/orders":
        user = handler._require_auth()
        if not user:
            return True
        uid = "" if user.role == "superadmin" else user.id
        if user.role == "superadmin" and (qs.get("user_id") or [""])[0]:
            uid = (qs.get("user_id") or [""])[0]
        handler._send_json(200, {"ok": True, "items": payment.list_orders(uid, limit=50)})
        return True

    if path.startswith("/api/payment/order/"):
        user = handler._require_auth()
        if not user:
            return True
        order_id = path.rsplit("/", 1)[-1].strip()
        try:
            uid = "" if user.role == "superadmin" else user.id
            order = payment.get_order(order_id, uid)
            handler._send_json(200, {"ok": True, "order": order})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/public/sync/progress":
        handler._send_json(200, {"ok": True, **sync_progress.get_progress()})
        return True

    if path == "/api/admin/extensions":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        handler._send_json(200, {
            "ok": True,
            "payment": payment.get_payment_settings(),
            "notifications": load_extensions().get("notifications", {}).get("settings", {}),
            "security": load_extensions().get("security", {}),
            "tenants": tenants.get_tenant_config(),
            "mall": cdk_extras.get_mall_settings(),
            "packages": cdk_extras.list_packages(),
            "api_keys": api_keys.list_api_keys(),
        })
        return True

    if path == "/api/admin/stats/charts":
        user = handler._require_auth()
        if not user:
            return True
        data = stats.full_dashboard_stats(SERVER.admin, SERVER.cdk)
        handler._send_json(200, {"ok": True, **data})
        return True

    if path == "/api/admin/stats/export.xlsx":
        token = handler._get_token() or (qs.get("token") or [""])[0]
        user = SERVER.admin.verify_token(token)
        if not user or user.role != "superadmin":
            handler._send_json(403, {"ok": False, "message": "需要超级管理员"})
            return True
        _export_excel(handler)
        return True

    if path == "/api/admin/sync/progress":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        handler._send_json(200, {"ok": True, **sync_progress.get_progress()})
        return True

    if path == "/api/admin/invoices":
        user = handler._require_auth()
        if not user:
            return True
        uid = "" if user.role == "superadmin" else user.id
        handler._send_json(200, {"ok": True, "items": agent_extras.list_invoices(uid)})
        return True

    if path.startswith("/api/admin/invoices/") and path.endswith("/html"):
        user = handler._require_auth()
        if not user:
            return True
        inv_id = path.split("/")[-2]
        invs = agent_extras.list_invoices()
        inv = next((i for i in invs if i.get("id") == inv_id), None)
        if not inv:
            handler._send_json(404, {"ok": False, "message": "发票不存在"})
            return True
        if user.role != "superadmin" and inv.get("user_id") != user.id:
            handler._send_json(403, {"ok": False, "message": "无权查看"})
            return True
        html = agent_extras.invoice_html(inv).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(html)))
        handler.end_headers()
        handler.wfile.write(html)
        return True

    if path == "/api/admin/games/preview":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        from web_server import run_async

        app_id = str((qs.get("appid") or [""])[0]).strip()
        try:
            result = run_async(SERVER.preview_manual_game(app_id))
            code = 200 if result.get("ok") else 400
            handler._send_json(code, result)
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/games/probe":
        user = handler._require_auth()
        if not user:
            return True
        from web_server import run_async

        app_id = str((qs.get("appid") or [""])[0]).strip()
        try:
            result = run_async(SERVER.probe_game_depot(app_id))
            code = 200 if result.get("ok") else 400
            handler._send_json(code, result)
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/agent/statement":
        user = handler._require_auth()
        if not user:
            return True
        month = (qs.get("month") or [""])[0]
        uid = (qs.get("user_id") or [user.id])[0]
        if user.role != "superadmin" and uid != user.id:
            handler._send_json(403, {"ok": False, "message": "无权查看"})
            return True
        try:
            stmt = agent_extras.monthly_statement(SERVER.admin, SERVER.cdk, uid, month)
            handler._send_json(200, {"ok": True, "statement": stmt})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/v1/games":
        user, _key = _verify_api_or_auth(handler, ["read", "games"], qs=qs)
        if user is None:
            return True
        q = (qs.get("q") or [""])[0]
        from web_server import run_async
        items = run_async(SERVER.search_games(q))
        handler._send_json(200, {"ok": True, "items": items})
        return True

    if path == "/api/v1/cdk/status":
        user, _key = _verify_api_or_auth(handler, ["read", "cdk"], qs=qs)
        if user is None:
            return True
        cdk = (qs.get("cdk") or [""])[0].strip()
        if not cdk:
            handler._send_json(400, {"ok": False, "message": "请提供 cdk 参数"})
            return True
        raw = SERVER.cdk.get_key_raw(cdk)
        if not raw:
            handler._send_json(404, {"ok": False, "message": "CDK 不存在"})
            return True
        if user.role == "agent" and str(raw.get("agent_id", "")) != user.id:
            handler._send_json(403, {"ok": False, "message": "无权查看该 CDK"})
            return True
        handler._send_json(200, {
            "ok": True,
            "cdk": cdk,
            "appid": raw.get("appid"),
            "name": raw.get("name"),
            "used": bool(raw.get("used")),
            "used_at": raw.get("used_at"),
            "revoked": bool(raw.get("revoked")),
            "billing_mode": raw.get("billing_mode"),
        })
        return True

    if path == "/api/v1/cdk/list":
        user, _key = _verify_api_or_auth(handler, ["read", "cdk"], qs=qs)
        if user is None:
            return True
        limit = max(1, min(int((qs.get("limit") or ["100"])[0]), 500))
        filt = (qs.get("filter") or ["all"])[0].strip().lower()
        items = SERVER.list_cdks(limit=limit, user=user if user.role == "agent" else None)
        if filt == "unused":
            items = [x for x in items if not x.get("used") and not x.get("revoked")]
        elif filt == "used":
            items = [x for x in items if x.get("used")]
        handler._send_json(200, {"ok": True, "items": items, "count": len(items)})
        return True

    if path == "/api/admin/agent/api-key":
        user = handler._require_auth()
        if not user:
            return True
        if user.role != "agent":
            handler._send_json(403, {"ok": False, "message": "仅代理可使用 API 密钥"})
            return True
        handler._send_json(200, _agent_api_key_payload(handler, user))
        return True

    if path == "/api/webhook/catalog-sync":
        secret = (qs.get("secret") or [""])[0]
        cfg = load_extensions()
        expected = str(cfg.get("webhook_secret") or payment.get_payment_settings().get("callback_secret") or "")
        if expected and secret != expected:
            handler._send_json(403, {"ok": False, "message": "Webhook secret 无效"})
            return True
        result = sync_progress.trigger_webhook_sync(handler.SERVER)
        handler._send_json(200, result)
        return True

    return False


def handle_post(handler: "WebHandler", path: str) -> bool:
    payload = handler._read_json_body()

    if path == "/api/public/mall/order":
        try:
            order = cdk_extras.create_mall_order(str(payload.get("package_id", "")), str(payload.get("contact", "")))
            handler._send_json(200, {"ok": True, "order": order})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/public/help-tutorial/verify-password":
        cfg = help_tutorial.get_help_tutorial()
        pwd = str(payload.get("password", "") or "").strip()
        secret = str(cfg.get("password_gate_secret", "") or "").strip()
        if not cfg.get("password_gate_enabled") or not secret:
            handler._send_json(200, {"ok": True})
            return True
        if pwd and pwd == secret:
            handler._send_json(200, {"ok": True})
        else:
            handler._send_json(403, {"ok": False, "message": "密码错误，请重新输入"})
        return True

    if path == "/api/admin/help-tutorial/save":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        try:
            saved = help_tutorial.update_help_tutorial(payload if isinstance(payload, dict) else {})
            handler._send_json(200, {"ok": True, "message": "已保存", "tutorial": saved})
        except Exception as e:
            _log.exception("save help tutorial failed")
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/payment/create":
        user = handler._require_auth()
        if not user:
            return True
        try:
            channel = str(payload.get("channel", "alipay")).lower()
            order = payment.create_order(
                user.id,
                user.username,
                float(payload.get("amount", 0)),
                channel,
                site_base=_api_base(handler),
                client_ip=handler._client_ip(),
                pay_type=str(payload.get("pay_type", "") or ""),
            )
            handler._send_json(200, {"ok": True, "order": order})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        except Exception as e:
            _log.exception("create payment order failed")
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/payment/callback":
        sig = handler.headers.get("X-Payment-Signature", "")
        if not payment.verify_callback_signature(payload, sig):
            handler._send_json(403, {"ok": False, "message": "签名无效"})
            return True
        try:
            order = payment.complete_order(str(payload.get("order_id", "")), str(payload.get("tx_id", "")))
            admin = SERVER.admin
            uid = order.get("user_id")
            amount = float(order.get("amount_cny", 0))
            with admin._write_lock():
                raw = admin.get_raw_user(uid)
                if raw:
                    raw["balance"] = round(float(raw.get("balance", 0)) + amount, 4)
            handler._send_json(200, {"ok": True, "message": "已到账"})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/extensions/save":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        section = str(payload.get("section", ""))
        data = payload.get("data") or {}
        if section == "payment":
            payment.update_payment_settings(data)
        elif section == "notifications":
            notification.update_settings(data)
        elif section == "security":
            security.update_security_settings(data)
        elif section == "tenants":
            tenants.update_tenant_settings(data.get("enabled"))
        elif section == "mall":
            cdk_extras.update_mall_settings(data)
        else:
            handler._send_json(400, {"ok": False, "message": "未知 section"})
            return True
        handler._send_json(200, {"ok": True, "message": "已保存"})
        return True

    if path == "/api/admin/extensions/test-notify":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        r = notification.send_message("测试通知", "这是一条来自 CS Steam 管理台的测试消息。")
        handler._send_json(200, {"ok": True, "results": r})
        return True

    if path == "/api/admin/payment/test-epay":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        try:
            result = payment.test_epay_connection()
            handler._send_json(200, result)
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/security/2fa/setup":
        user = handler._require_auth()
        if not user:
            return True
        info = security.setup_2fa(user.id)
        handler._send_json(200, {"ok": True, **info})
        return True

    if path == "/api/admin/security/2fa/enable":
        user = handler._require_auth()
        if not user:
            return True
        try:
            security.enable_2fa(user.id, str(payload.get("code", "")))
            handler._send_json(200, {"ok": True, "message": "2FA 已启用"})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/security/2fa/disable":
        user = handler._require_auth()
        if not user:
            return True
        security.disable_2fa(user.id)
        handler._send_json(200, {"ok": True, "message": "2FA 已关闭"})
        return True

    if path == "/api/admin/api-keys/create":
        user = handler._require_auth()
        if not user:
            return True
        info = api_keys.create_api_key(str(payload.get("name", "default")), user.id, payload.get("scopes"))
        handler._send_json(200, {"ok": True, **info, "message": "请妥善保存 Key，仅显示一次"})
        return True

    if path == "/api/admin/api-keys/revoke":
        user = handler._require_auth()
        if not user:
            return True
        api_keys.revoke_api_key(str(payload.get("id", "")), user.id if user.role != "superadmin" else "")
        handler._send_json(200, {"ok": True, "message": "已吊销"})
        return True

    if path == "/api/admin/cdk/batch-import":
        user = handler._require_auth()
        if not user:
            return True
        if security.require_confirm_password() and not payload.get("confirm_password"):
            handler._send_json(400, {"ok": False, "message": "敏感操作需 confirm_password"})
            return True
        try:
            r = cdk_extras.batch_import_cdks(
                SERVER.cdk,
                str(payload.get("lines", "")),
                str(payload.get("appid", "")),
                user.id if user.role == "agent" else str(payload.get("agent_id", "")),
                user.username,
            )
            handler._send_json(200, {"ok": True, **r})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/cdk/packages":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        if payload.get("delete"):
            cdk_extras.delete_package(str(payload.get("id", "")))
            handler._send_json(200, {"ok": True, "message": "已删除"})
        else:
            pkg = cdk_extras.save_package(payload)
            handler._send_json(200, {"ok": True, "package": pkg})
        return True

    if path == "/api/admin/mall/fulfill":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        try:
            order = cdk_extras.fulfill_mall_order(str(payload.get("order_id", "")), SERVER.cdk)
            handler._send_json(200, {"ok": True, "order": order})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/tenants/add":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        try:
            site = tenants.add_site(str(payload.get("name", "")), str(payload.get("domain", "")), str(payload.get("site_name", "")))
            handler._send_json(200, {"ok": True, "site": site})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/agent/credit":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        try:
            u = agent_extras.set_credit_limit(SERVER.admin, str(payload.get("user_id", "")), float(payload.get("credit_limit", 0)))
            handler._send_json(200, {"ok": True, "user": u})
        except ValueError as e:
            handler._send_json(400, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/invoices/create":
        user = handler._require_auth()
        if not user:
            return True
        uid = str(payload.get("user_id") or user.id)
        if user.role != "superadmin" and uid != user.id:
            handler._send_json(403, {"ok": False, "message": "无权操作"})
            return True
        inv = agent_extras.create_invoice(SERVER.admin, SERVER.cdk, uid, str(payload.get("month", "")), str(payload.get("note", "")))
        handler._send_json(200, {"ok": True, "invoice": inv})
        return True

    if path == "/api/admin/games/import":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        from web_server import run_async

        app_id = str(payload.get("appid", "")).strip()
        try:
            result = run_async(SERVER.import_game_plugins(app_id))
            code = 200 if result.get("ok") else 400
            handler._send_json(code, result)
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/games/enrich-meta":
        user = handler._require_auth()
        if not user:
            return True
        from urllib.parse import parse_qs, urlparse

        from web_server import run_async

        qs = parse_qs(urlparse(handler.path).query)
        app_id = str(payload.get("appid") or (qs.get("appid") or [""])[0]).strip()
        force = bool(payload.get("force") or str((qs.get("force") or ["0"])[0]).lower() in ("1", "true", "yes"))
        all_manifest = bool(
            payload.get("all_manifest")
            or str((qs.get("all_manifest") or ["0"])[0]).lower() in ("1", "true", "yes")
        )
        limit = int(payload.get("limit") or (qs.get("limit") or ["0"])[0] or 0)
        try:
            result = run_async(
                SERVER.enrich_game_meta(
                    app_id=app_id,
                    force=force,
                    all_manifest=all_manifest,
                    limit=limit,
                )
            )
            handler._send_json(200 if result.get("ok") else 400, result)
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/games/add":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        from web_server import run_async

        app_id = str(payload.get("appid", "")).strip()
        name = str(payload.get("name", "") or payload.get("game_name", "")).strip()
        force = bool(payload.get("force"))
        probe = payload.get("probe", True) is not False
        try_import = bool(payload.get("try_import") or payload.get("generate_lua"))
        try:
            result = run_async(
                SERVER.add_manual_manifest_game(
                    app_id,
                    name=name,
                    force=force,
                    probe=probe,
                    try_import=try_import,
                    operator=user.username,
                )
            )
            code = 200 if result.get("ok") else 400
            handler._send_json(code, result)
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path == "/api/admin/games/uninstall":
        user = handler._require_auth(superadmin_only=True)
        if not user:
            return True
        from web_server import run_async
        run_async(SERVER.ensure_ready())
        sp = SERVER.service.backend.steam_path
        r = game_ops.uninstall_game(sp, str(payload.get("appid", "")))
        handler._send_json(200, r)
        return True

    if path == "/api/admin/games/check-updates":
        user = handler._require_auth()
        if not user:
            return True
        from web_server import run_async
        run_async(SERVER.ensure_ready())
        sp = SERVER.service.backend.steam_path
        ids = payload.get("appids") or []
        r = game_ops.check_game_updates(sp, ids)
        handler._send_json(200, {"ok": True, "items": r})
        return True

    if path == "/api/admin/games/import-dlc":
        user = handler._require_auth()
        if not user:
            return True
        from web_server import run_async
        app_id = str(payload.get("appid", ""))
        dlcs = payload.get("dlc_ids") or []
        r = run_async(game_ops.import_dlc_batch(SERVER.service, app_id, dlcs))
        handler._send_json(200, r)
        return True

    if path == "/api/admin/games/workshop":
        user = handler._require_auth()
        if not user:
            return True
        from web_server import run_async
        run_async(SERVER.ensure_ready())
        sp = SERVER.service.backend.steam_path
        r = game_ops.enable_workshop_stub(sp, str(payload.get("appid", "")))
        handler._send_json(200, r)
        return True

    if path == "/api/web-box/import":
        user = handler._require_auth()
        if not user:
            return True
        from web_server import run_async
        app_id = str(payload.get("appid", "")).strip()
        try:
            ok_import, msg = run_async(SERVER.service.check_importable(app_id, deep_probe=True))
            if not ok_import:
                handler._send_json(400, {"ok": False, "message": msg})
                return True
            from box_service import ImportOptions
            result = run_async(SERVER.service.import_game_with_fallback(app_id, None, ImportOptions()))
            handler._send_json(200, {"ok": result.ok, "message": result.message, "appid": app_id})
        except Exception as e:
            handler._send_json(500, {"ok": False, "message": str(e)})
        return True

    if path in ("/api/admin/agent/api-key/generate", "/api/admin/agent/api-key/regenerate"):
        user = handler._require_auth()
        if not user:
            return True
        if user.role != "agent":
            handler._send_json(403, {"ok": False, "message": "仅代理可使用 API 密钥"})
            return True
        from extensions.api_keys import regenerate_agent_api_key

        created = regenerate_agent_api_key(user.id, user.username)
        payload = _agent_api_key_payload(handler, user, api_key=created.get("api_key"))
        payload["message"] = (
            "API密钥生成成功" if path.endswith("/generate") else "API密钥重新生成成功"
        )
        handler._send_json(200, payload)
        return True

    if path == "/api/admin/agent/api-key/clear":
        user = handler._require_auth()
        if not user:
            return True
        if user.role != "agent":
            handler._send_json(403, {"ok": False, "message": "仅代理可使用 API 密钥"})
            return True
        from extensions.api_keys import clear_agent_api_key

        clear_agent_api_key(user.id)
        handler._send_json(200, {"ok": True, "message": "API密钥清除成功", "api_key": None, "has_api_key": False})
        return True

    if path == "/api2/cdkeys/generate":
        _handle_api2_generate_steamox(handler, payload)
        return True

    if path == "/api/v1/cdk/generate":
        user, _key = _verify_api_or_auth(handler, ["cdk"], payload=payload)
        if user is None:
            return True
        if not _extract_api_key_raw(handler, payload) and not _key:
            handler._send_json(401, {"ok": False, "message": "请提供 api_key"})
            return True
        _handle_cdk_generate_api(handler, payload, user)
        return True

    return False


def _export_excel(handler: "WebHandler") -> None:
    import csv
    import io

    data = stats.full_dashboard_stats(SERVER.admin, SERVER.cdk)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日期", "创建CDK", "使用CDK"])
    for row in data.get("cdk_daily", []):
        w.writerow([row.get("date"), row.get("created"), row.get("used")])
    body = buf.getvalue().encode("utf-8-sig")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/csv; charset=utf-8")
    handler.send_header("Content-Disposition", 'attachment; filename="stats_export.csv"')
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
