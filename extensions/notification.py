"""通知：邮件 / Telegram / 企业微信。"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import httpx

from extensions.store import load_extensions

_log = logging.getLogger(__name__)


def _settings() -> Dict[str, Any]:
    return load_extensions().get("notifications", {}).get("settings", {})


def send_message(title: str, body: str) -> Dict[str, Any]:
    s = _settings()
    results: Dict[str, Any] = {}
    if s.get("telegram_enabled") and s.get("telegram_bot_token") and s.get("telegram_chat_id"):
        results["telegram"] = _send_telegram(title, body, s)
    if s.get("wecom_enabled") and s.get("wecom_webhook"):
        results["wecom"] = _send_wecom(title, body, s)
    if s.get("email_enabled") and s.get("smtp_host") and s.get("smtp_from"):
        results["email"] = _send_email(title, body, s)
    return results


def _send_telegram(title: str, body: str, s: Dict[str, Any]) -> bool:
    token = str(s.get("telegram_bot_token", ""))
    chat_id = str(s.get("telegram_chat_id", ""))
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"*{title}*\n{body}"
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
            return r.status_code == 200
    except Exception as e:
        _log.warning("Telegram 通知失败: %s", e)
        return False


def _send_wecom(title: str, body: str, s: Dict[str, Any]) -> bool:
    url = str(s.get("wecom_webhook", ""))
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(url, json={"msgtype": "text", "text": {"content": f"{title}\n{body}"}})
            return r.status_code == 200
    except Exception as e:
        _log.warning("企业微信通知失败: %s", e)
        return False


def _send_email(title: str, body: str, s: Dict[str, Any]) -> bool:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = str(s.get("smtp_from", ""))
    to_addr = str(s.get("smtp_to") or s.get("smtp_from", ""))
    msg["To"] = to_addr
    host = str(s.get("smtp_host", ""))
    port = int(s.get("smtp_port") or 465)
    user = str(s.get("smtp_user", ""))
    password = str(s.get("smtp_password", ""))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        _log.warning("邮件通知失败: %s", e)
        return False


def notify_recharge_pending(username: str, amount: float) -> None:
    s = _settings()
    if not s.get("notify_recharge"):
        return
    send_message("充值待审核", f"代理 {username} 申请充值 ¥{amount:.2f}")


def notify_withdraw_pending(username: str, amount: float) -> None:
    s = _settings()
    if not s.get("notify_withdraw"):
        return
    send_message("提现待审核", f"代理 {username} 申请提现 ¥{amount:.2f}")


def notify_sync_failed(error: str) -> None:
    s = _settings()
    if not s.get("notify_sync_fail"):
        return
    send_message("清单同步失败", error[:500])


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    from extensions.store import load_extensions, save_extensions

    data = load_extensions()
    settings = data.setdefault("notifications", {}).setdefault("settings", {})
    settings.update(patch)
    save_extensions(data)
    return settings
