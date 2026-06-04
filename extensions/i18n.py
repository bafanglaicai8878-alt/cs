"""简易多语言（zh / en）。"""

from __future__ import annotations

from typing import Any, Dict

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "zh": {
        "dashboard": "仪表盘",
        "cdk": "CDK 管理",
        "games": "游戏库",
        "settings": "系统设置",
        "extensions": "扩展中心",
        "login": "登录",
        "logout": "退出",
        "save": "保存",
        "cancel": "取消",
        "search": "搜索",
        "balance": "余额",
        "quota": "配额",
        "recharge": "充值",
        "withdraw": "提现",
        "sync_now": "立即同步",
        "shop": "卡密商城",
        "user_center": "用户中心",
        "web_box": "Web 入库",
    },
    "en": {
        "dashboard": "Dashboard",
        "cdk": "CDK",
        "games": "Games",
        "settings": "Settings",
        "extensions": "Extensions",
        "login": "Login",
        "logout": "Logout",
        "save": "Save",
        "cancel": "Cancel",
        "search": "Search",
        "balance": "Balance",
        "quota": "Quota",
        "recharge": "Recharge",
        "withdraw": "Withdraw",
        "sync_now": "Sync Now",
        "shop": "CDK Shop",
        "user_center": "User Center",
        "web_box": "Web Import",
    },
}


def t(key: str, lang: str = "zh") -> str:
    lang = lang if lang in TRANSLATIONS else "zh"
    return TRANSLATIONS[lang].get(key, TRANSLATIONS["zh"].get(key, key))


def get_all(lang: str = "zh") -> Dict[str, str]:
    lang = lang if lang in TRANSLATIONS else "zh"
    return dict(TRANSLATIONS[lang])
