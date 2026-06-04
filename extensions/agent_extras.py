"""代理扩展：信用额度、月结、发票。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from extensions.store import load_extensions, save_extensions


def get_credit_limit(user_raw: Dict[str, Any]) -> float:
    return float(user_raw.get("credit_limit") or 0)


def set_credit_limit(admin_service, user_id: str, limit: float) -> Dict[str, Any]:
    raw = admin_service.get_raw_user(user_id)
    if not raw:
        raise ValueError("用户不存在")
    raw["credit_limit"] = round(max(0, float(limit)), 2)
    admin_service.save()
    return admin_service._user_public(raw)


def available_with_credit(user_raw: Dict[str, Any]) -> float:
    balance = float(user_raw.get("balance") or 0)
    credit = get_credit_limit(user_raw)
    return round(balance + credit, 4)


def monthly_statement(admin_service, cdk_service, user_id: str, month: str = "") -> Dict[str, Any]:
    month = month or datetime.now().strftime("%Y-%m")
    raw = admin_service.get_raw_user(user_id)
    if not raw:
        raise ValueError("用户不存在")
    keys = cdk_service._data.get("keys", {})
    created = used = 0
    cost = 0.0
    price = float(raw.get("cdk_cost_price") or admin_service.base_cdk_price())
    for k in keys.values():
        if str(k.get("agent_id", "")) != user_id:
            continue
        ca = str(k.get("created_at", ""))[:7]
        if ca == month:
            created += 1
            if k.get("charged"):
                cost += price
        if k.get("used") and str(k.get("used_at", ""))[:7] == month:
            used += 1
    commissions = [
        c for c in admin_service.list_commission_logs(user_id, limit=500)
        if str(c.get("created_at", ""))[:7] == month
    ]
    commission_total = sum(float(c.get("amount") or 0) for c in commissions)
    return {
        "month": month,
        "user_id": user_id,
        "username": raw.get("username"),
        "cdk_created": created,
        "cdk_used": used,
        "cdk_cost": round(cost, 4),
        "commission_earned": round(commission_total, 4),
        "balance_end": round(float(raw.get("balance") or 0), 4),
        "credit_limit": get_credit_limit(raw),
    }


def create_invoice(admin_service, cdk_service, user_id: str, month: str, note: str = "") -> Dict[str, Any]:
    stmt = monthly_statement(admin_service, cdk_service, user_id, month)
    data = load_extensions()
    invoices = data.setdefault("invoices", [])
    inv_id = f"INV-{month.replace('-','')}-{user_id[:6].upper()}"
    inv = {
        "id": inv_id,
        "user_id": user_id,
        "username": stmt.get("username"),
        "month": month,
        "cdk_created": stmt.get("cdk_created"),
        "cdk_used": stmt.get("cdk_used"),
        "amount": stmt.get("cdk_cost"),
        "note": note,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "issued",
    }
    invoices.append(inv)
    save_extensions(data)
    return inv


def list_invoices(user_id: str = "") -> List[Dict[str, Any]]:
    invs = load_extensions().get("invoices") or []
    if user_id:
        invs = [i for i in invs if i.get("user_id") == user_id]
    return sorted(invs, key=lambda x: x.get("created_at", ""), reverse=True)


def invoice_html(invoice: Dict[str, Any]) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>发票 {invoice.get('id')}</title>
<style>body{{font-family:sans-serif;max-width:640px;margin:40px auto;padding:20px}}
table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:8px}}</style></head>
<body><h1>月结账单 / Invoice</h1>
<p>编号：{invoice.get('id')}<br>账期：{invoice.get('month')}<br>代理：{invoice.get('username')}</p>
<table><tr><th>项目</th><th>数量</th></tr>
<tr><td>生成 CDK</td><td>{invoice.get('cdk_created')}</td></tr>
<tr><td>激活 CDK</td><td>{invoice.get('cdk_used')}</td></tr>
<tr><td>消费金额 (CNY)</td><td>{invoice.get('amount')}</td></tr></table>
<p>开具时间：{invoice.get('created_at')}</p></body></html>"""
