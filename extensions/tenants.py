"""多租户 / 多域名站点。"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from extensions.store import load_extensions, save_extensions


def get_tenant_config() -> Dict[str, Any]:
    return load_extensions().get("tenants", {})


def list_sites() -> List[Dict[str, Any]]:
    return list(get_tenant_config().get("sites") or [])


def add_site(name: str, domain: str, site_name: str = "") -> Dict[str, Any]:
    domain = domain.strip().lower().split(":")[0]
    if not domain:
        raise ValueError("域名不能为空")
    data = load_extensions()
    tenants = data.setdefault("tenants", {})
    sites = tenants.setdefault("sites", [])
    for s in sites:
        if s.get("domain") == domain:
            raise ValueError("域名已存在")
    site = {
        "id": secrets.token_hex(6),
        "name": name,
        "domain": domain,
        "site_name": site_name or name,
        "enabled": True,
    }
    sites.append(site)
    save_extensions(data)
    return site


def resolve_tenant(host: str) -> Optional[Dict[str, Any]]:
    cfg = get_tenant_config()
    if not cfg.get("enabled"):
        return None
    host = host.strip().lower().split(":")[0]
    for site in cfg.get("sites") or []:
        if site.get("enabled") and site.get("domain") == host:
            return site
    return None


def update_tenant_settings(enabled: Optional[bool] = None) -> Dict[str, Any]:
    data = load_extensions()
    tenants = data.setdefault("tenants", {})
    if enabled is not None:
        tenants["enabled"] = bool(enabled)
    save_extensions(data)
    return tenants
