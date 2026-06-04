"""CDK 扩展：批量导入、套餐、商城。"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from extensions.store import load_extensions, save_extensions


def list_packages() -> List[Dict[str, Any]]:
    return list(load_extensions().get("cdk_packages") or [])


def save_package(pkg: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    packages = data.setdefault("cdk_packages", [])
    pid = str(pkg.get("id") or uuid.uuid4().hex[:8])
    item = {
        "id": pid,
        "name": str(pkg.get("name", "")),
        "appid": str(pkg.get("appid", "")),
        "price_cny": float(pkg.get("price_cny") or 0),
        "quota": int(pkg.get("quota") or 1),
        "expire_days": int(pkg.get("expire_days") or 0),
        "enabled": bool(pkg.get("enabled", True)),
        "description": str(pkg.get("description", "")),
    }
    found = False
    for i, p in enumerate(packages):
        if p.get("id") == pid:
            packages[i] = item
            found = True
            break
    if not found:
        packages.append(item)
    save_extensions(data)
    return item


def delete_package(package_id: str) -> bool:
    data = load_extensions()
    packages = data.get("cdk_packages") or []
    new_list = [p for p in packages if p.get("id") != package_id]
    if len(new_list) == len(packages):
        return False
    data["cdk_packages"] = new_list
    save_extensions(data)
    return True


def get_mall_settings() -> Dict[str, Any]:
    return load_extensions().get("mall_settings", {})


def update_mall_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    ms = data.setdefault("mall_settings", {})
    ms.update(patch)
    save_extensions(data)
    return ms


def batch_import_cdks(cdk_service, lines: str, appid: str, agent_id: str = "", created_by: str = "") -> Dict[str, Any]:
    appid = str(appid).strip()
    if not appid.isdigit():
        raise ValueError("AppID 无效")
    added, skipped, errors = [], [], []
    for line in str(lines or "").splitlines():
        code = cdk_service.normalize_cdk(line.strip())
        if not code:
            continue
        try:
            cdk_service.add_key(appid, cdk=code, agent_id=agent_id, created_by=created_by, defer_save=True)
            added.append(code)
        except ValueError as e:
            if "已存在" in str(e):
                skipped.append(code)
            else:
                errors.append(f"{code}: {e}")
    if added:
        cdk_service.save()
    return {"added": len(added), "skipped": len(skipped), "errors": errors, "codes": added[:20]}


def create_mall_order(package_id: str, buyer_contact: str = "") -> Dict[str, Any]:
    packages = {p["id"]: p for p in list_packages()}
    pkg = packages.get(package_id)
    if not pkg or not pkg.get("enabled"):
        raise ValueError("套餐不存在或已下架")
    order_id = secrets.token_hex(8).upper()
    data = load_extensions()
    orders = data.setdefault("mall_orders", [])
    order = {
        "id": order_id,
        "package_id": package_id,
        "package_name": pkg.get("name"),
        "appid": pkg.get("appid"),
        "price_cny": pkg.get("price_cny"),
        "buyer_contact": buyer_contact,
        "status": "pending",
        "cdk": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    orders.append(order)
    save_extensions(data)
    return order


def fulfill_mall_order(order_id: str, cdk_service, agent_id: str = "") -> Dict[str, Any]:
    data = load_extensions()
    orders = data.get("mall_orders") or []
    order = next((o for o in orders if o.get("id") == order_id), None)
    if not order:
        raise ValueError("订单不存在")
    if order.get("status") == "fulfilled":
        return order
    appid = str(order.get("appid", ""))
    code = cdk_service.add_key(appid, name=order.get("package_name", ""), agent_id=agent_id, note="商城订单")
    order["status"] = "fulfilled"
    order["cdk"] = code
    order["fulfilled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_extensions(data)
    return order
