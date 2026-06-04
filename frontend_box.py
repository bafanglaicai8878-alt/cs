"""Steam 游戏盒子 - 封面网格 UI。"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

from box_service import (
    AsyncLoopRunner,
    BoxService,
    BulkImportResult,
    CdkActivationResult,
    EnvironmentInfo,
    GameSearchResult,
    ImportOptions,
    ImportResult,
    ManifestGameEntry,
    ManifestSource,
)
from client_auth_service import ClientAuthService
from game_catalog import CARD_H, CARD_W, FEATURED_APPIDS, GameCardInfo

APP_TITLE = "PLAYGAME"
WINDOW_SIZE = "1400x900"
PAGE_SIZE = 48
GRID_COLS = 4
PG_CARD_W, PG_CARD_H = 272, 153

# 精致暗色主题
BG = "#0b0e14"
SIDEBAR_BG = "#12151c"
SIDEBAR_BORDER = "#1e2430"
TOPBAR_BG = "#12151c"
CONTENT_BG = "#0b0e14"
SURFACE = "#171b24"
SURFACE_HI = "#222836"
BORDER = "#2a3140"
NAV_ACTIVE_BG = "#1a2744"
NAV_HOVER = "#1a1f2b"
NAV_WIDTH = 224
NAV_TEXT = "#8b93a7"
NAV_TEXT_HI = "#eef1f6"
ACCENT = "#4d8dff"
ACCENT_DARK = "#3a73e0"
ACCENT_GLOW = "#6ea1ff"
STEAM_BLUE = ACCENT
SLOGAN_YELLOW = "#e8b84a"
CARD_BG = SURFACE
CARD_BORDER = BORDER
MUTED = "#6b7280"
SUCCESS = "#3ecf8e"
VIP_GOLD = "#f0b429"
SEARCH_BG = "#0f131a"
TOPBAR_H = 56
SIDEBAR_ACCENT = NAV_ACTIVE_BG
SIDEBAR_TEXT = NAV_TEXT
SIDEBAR_TEXT_HI = NAV_TEXT_HI
NAV_ACTIVE = NAV_ACTIVE_BG
HERO_H = 240
FONT = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI"
FONT_UI = FONT
FONT_TITLE = (FONT, 14, "bold")
FONT_SECTION = (FONT, 12, "bold")
FONT_BODY = (FONT, 10)
FONT_SMALL = (FONT, 9)
ENTRY_BG = SEARCH_BG
ENTRY_FG = NAV_TEXT_HI
DANGER = "#f87171"
WARN = "#fbbf24"
AUTH_CARD_BG = SURFACE

DEV_GAME_NAMES = {
    "730": "Counter-Strike 2",
    "570": "Dota 2",
    "1145360": "Hades II",
    "413150": "Stardew Valley",
    "105600": "Terraria",
    "1091500": "Cyberpunk 2077",
    "1245620": "ELDEN RING",
    "1174180": "Red Dead Redemption 2",
    "271590": "Grand Theft Auto V",
    "892970": "Valheim",
    "990080": "Hollow Knight: Silksong",
}

DEV_HERO_GAMES = [
    ("1245620", "艾尔登法环"),
    ("1091500", "赛博朋克 2077"),
    ("1174180", "荒野大镖客2"),
    ("271590", "GTA V"),
    ("1145360", "Hades II"),
]

CATEGORIES = [
    "热门推荐", "射击游戏", "建设经营", "轻松娱乐", "3A经典",
    "下载周榜", "下载总榜", "最新上线", "近期更新",
]

VIEW_ALIASES = {
    "online": "catalog", "battle": "catalog", "single": "catalog",
    "switch": "catalog", "retro": "catalog", "mobile": "catalog",
    "favorites": "mine",
}


class GameCardWidget(tk.Frame):
    """游戏卡片：圆角边框感 + 悬停高亮 + 底部渐变标题。"""

    def __init__(
        self, master, card: GameCardInfo, on_select, on_import,
        import_enabled: bool = True, on_locked=None, allow_probe: bool = False, **kw
    ):
        padx = kw.pop("padx", 8)
        pady = kw.pop("pady", 10)
        super().__init__(master, bg=CONTENT_BG, highlightthickness=0, **kw)
        self.card = card
        self._photo = None
        self.on_select = on_select
        self.on_import = on_import
        self.on_locked = on_locked
        self._selected = False

        shell = tk.Frame(
            self, bg=SURFACE, width=PG_CARD_W, height=PG_CARD_H,
            highlightthickness=1, highlightbackground=BORDER,
        )
        shell.pack(padx=padx, pady=pady)
        shell.pack_propagate(False)

        cover_wrap = tk.Frame(shell, bg=SURFACE_HI, width=PG_CARD_W, height=PG_CARD_H)
        cover_wrap.pack(fill="both", expand=True)
        cover_wrap.pack_propagate(False)

        self.cover_label = tk.Label(
            cover_wrap, bg=SURFACE_HI, text="", fg=MUTED, font=FONT_SMALL,
        )
        self.cover_label.place(relx=0.5, rely=0.5, anchor="center")
        self._placeholder = tk.Label(
            cover_wrap, bg=SURFACE_HI, text="加载中", fg=MUTED, font=FONT_SMALL,
        )
        self._placeholder.place(relx=0.5, rely=0.5, anchor="center")

        size_val = f"{40 + int(card.appid or 0) % 90}.{int(card.appid or 0) % 9}G"
        size_badge = tk.Frame(cover_wrap, bg="#000000")
        size_badge.place(relx=1.0, x=-8, y=8, anchor="ne")
        tk.Label(
            size_badge, text=size_val, bg="#000000", fg="#d1d5db", font=(FONT, 8),
        ).pack(padx=7, pady=3)

        if card.installed:
            badge = tk.Frame(cover_wrap, bg=ACCENT)
            badge.place(x=8, y=8)
            tk.Label(badge, text="已入库", bg=ACCENT, fg="#fff", font=(FONT, 8)).pack(padx=6, pady=2)
        elif card.in_manifest:
            badge = tk.Frame(cover_wrap, bg=SUCCESS)
            badge.place(x=8, y=8)
            tk.Label(badge, text="可入库", bg=SUCCESS, fg="#0b0e14", font=(FONT, 8)).pack(padx=6, pady=2)

        shade = tk.Frame(cover_wrap, bg="#000000")
        shade.place(relx=0, rely=1.0, anchor="sw", relwidth=1, height=48)

        title = card.display_title
        if len(title) > 20:
            title = title[:19] + "…"
        self.title_label = tk.Label(
            shade, text=title, bg="#000000", fg=NAV_TEXT_HI,
            font=(FONT, 10, "bold"), anchor="w",
        )
        self.title_label.pack(fill="x", padx=12, pady=(8, 4))

        genre_txt = card.genre or "动作冒险"
        tk.Label(
            shade, text=genre_txt, bg="#000000", fg=MUTED, font=FONT_SMALL, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 6))

        self._shell = shell
        can_import = card.in_manifest or card.installed or allow_probe
        targets = (self, shell, cover_wrap, self.cover_label, self.title_label, shade)
        for w in targets:
            w.bind("<Button-1>", lambda _e, a=card.appid: on_select(a))
            w.bind("<Enter>", lambda _e: self._set_hover(True))
            w.bind("<Leave>", lambda _e: self._set_hover(False))
            if can_import and import_enabled:
                w.bind("<Double-1>", lambda _e, a=card.appid: on_import(a))
            elif can_import and on_locked:
                w.bind("<Double-1>", lambda _e: on_locked())

    def _set_hover(self, on: bool) -> None:
        if self._selected:
            return
        self._shell.config(highlightbackground=ACCENT if on else BORDER)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._shell.config(highlightbackground=ACCENT if selected else BORDER, highlightthickness=2 if selected else 1)

    def set_photo(self, photo) -> None:
        self._photo = photo
        self.cover_label.config(image=photo, text="")
        self.cover_label.image = photo
        self._placeholder.place_forget()


class GameBoxApp(tk.Tk):
    def __init__(self, start_view: str = "home", auto_prepare_cdk: bool = False, ui_dev: bool = False):
        super().__init__()
        self.title(APP_TITLE + (" · UI开发" if ui_dev else ""))
        self.geometry(WINDOW_SIZE)
        self.minsize(1024, 640)
        self.configure(bg=BG)

        self._ui_dev = ui_dev
        self.runner = AsyncLoopRunner()
        self.service = BoxService()
        self.auth = ClientAuthService()
        self._box_server_url = ""
        self._config: dict = {}
        self._session_token = ""
        self.current_user: Optional[dict] = None
        self._start_view = start_view
        self._auto_prepare_cdk = auto_prepare_cdk
        self._bootstrap_done = False

        self.manifest_sources: List[ManifestSource] = []
        self.cards: List[GameCardInfo] = []
        self.selected_app_id: Optional[str] = None
        self.current_view = tk.StringVar(value="home")
        self.list_page = 1
        self.list_total = 0
        self.list_total_pages = 1
        self.list_hint = ""
        self._photos: Dict[str, object] = {}
        self._import_all_running = False
        self._active_nav = "home"
        self._active_category = "热门推荐"
        self._hero_appid = DEV_HERO_GAMES[0][0]
        self._hero_photos: Dict[str, object] = {}
        self._hero_list_labels: List[tk.Label] = []
        self._hero_list_rows: List[tk.Frame] = []
        self._layout_ready = False
        self._card_widgets: Dict[str, GameCardWidget] = {}

        self.auto_fallback_var = tk.BooleanVar(value=True)
        self.add_dlc_var = tk.BooleanVar(value=True)
        self.auto_update_var = tk.BooleanVar(value=False)
        self.auto_finalize_var = tk.BooleanVar(value=True)

        self._build_styles()
        self._build_layout()
        self._build_auth_overlay()
        if self._ui_dev:
            self.after(100, self._start_ui_dev)
        else:
            self.after(100, self._try_auto_login)

    def _dev_mock_cards(self, page: int = 1) -> List[GameCardInfo]:
        start = max(0, (page - 1) * PAGE_SIZE)
        ids = FEATURED_APPIDS[start : start + PAGE_SIZE]
        cards: List[GameCardInfo] = []
        for i, appid in enumerate(ids):
            cards.append(
                GameCardInfo(
                    appid=appid,
                    name=DEV_GAME_NAMES.get(appid, f"示例游戏 {appid}"),
                    genre=["动作", "独立", "RPG", "策略"][i % 4],
                    in_manifest=True,
                    installed=(i % 7 == 0),
                )
            )
        return cards

    def _start_ui_dev(self) -> None:
        """Mac / 本地 UI 开发：跳过 Steam 检测与登录，加载示例数据。"""
        self.auth_overlay.pack_forget()
        self.main_body.pack(fill="both", expand=True)
        self.current_user = {
            "id": "ui-dev",
            "username": "ui_dev",
            "display_name": "UI 开发者",
            "vip": True,
            "vip_expires_at": "2099-12-31",
        }
        self._update_user_panel()
        self._bootstrap_done = True
        self.manifest_sources = [
            ManifestSource(key="dev", name="ManifestHub（开发示例）", kind="builtin_github", repo="SteamAutoCracks/ManifestHub"),
        ]
        self.source_combo["values"] = [s.name for s in self.manifest_sources]
        if self.manifest_sources:
            self.source_combo.current(0)
        self.list_total_pages = max(1, (len(FEATURED_APPIDS) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.set_busy(False, "UI 开发模式 · 无需 Steam")
        self.after(300, self._load_all_hero_covers)
        self._switch_view(self._start_view)

    def _ui_dev_notice(self, action: str = "此操作") -> None:
        messagebox.showinfo("UI 开发模式", f"{action}已在开发模式下跳过。\n请在 Windows 客户端验证真实逻辑。")

    def _reload_view_dev(self) -> None:
        view = self.current_view.get()
        if view == "settings":
            return
        self.set_busy(True, "加载示例…")
        query = self.search_var.get().strip()
        if query == getattr(self, "_search_placeholder", ""):
            query = ""
        cards = self._dev_mock_cards(self.list_page)
        if query:
            q = query.lower()
            cards = [c for c in cards if q in c.display_title.lower() or q in c.appid]
        elif view == "home" and self._active_category != "热门推荐":
            cat_genre = {
                "射击游戏": "动作", "建设经营": "策略", "轻松娱乐": "独立",
                "3A经典": "RPG", "下载周榜": "动作", "下载总榜": "RPG",
                "最新上线": "独立", "近期更新": "策略",
            }.get(self._active_category)
            if cat_genre:
                cards = [c for c in cards if c.genre == cat_genre]
        self.cards = cards
        self.list_total = len(FEATURED_APPIDS)
        self.list_hint = "UI 开发模式 · 示例数据"
        self._render_grid(cards)
        self.list_info_label.config(
            text=f"{self.list_hint} · 第 {self.list_page}/{self.list_total_pages} 页 · 本页 {len(cards)} 款"
        )
        if hasattr(self, "page_label"):
            self.page_label.config(text=f"{self.list_page} / {self.list_total_pages}")
        self.set_busy(False, "UI 开发模式")

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=CONTENT_BG)
        style.configure("TLabel", background=CONTENT_BG, foreground=NAV_TEXT_HI, font=FONT_SMALL)
        style.configure(
            "TCombobox",
            fieldbackground=SURFACE, background=SURFACE, foreground=NAV_TEXT_HI,
            bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        )
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)])
        style.configure(
            "Vertical.TScrollbar",
            background=SURFACE_HI, troughcolor=CONTENT_BG,
            bordercolor=BORDER, arrowcolor=NAV_TEXT,
        )
        style.configure(
            "Dark.TCheckbutton",
            background=CARD_BG, foreground=NAV_TEXT_HI,
            font=FONT_SMALL, focuscolor=CARD_BG,
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", SURFACE_HI), ("selected", CARD_BG)],
            foreground=[("disabled", MUTED)],
        )

    def _surface_card(self, parent, *, padx: int = 24, pady: int = 24) -> tk.Frame:
        return tk.Frame(
            parent, bg=SURFACE, padx=padx, pady=pady,
            highlightthickness=1, highlightbackground=BORDER,
        )

    def _dark_entry(self, parent, textvariable=None, **kw) -> tk.Entry:
        opts = dict(
            relief="flat", bg=ENTRY_BG, fg=ENTRY_FG,
            insertbackground=ACCENT_GLOW, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            font=kw.pop("font", FONT_BODY),
        )
        opts.update(kw)
        return tk.Entry(parent, textvariable=textvariable, **opts)

    def _form_label(self, parent, text: str, width: int = 8) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=parent.cget("bg"), fg=NAV_TEXT_HI,
            width=width, anchor="w", font=FONT_SMALL,
        )

    def _page_header(self, parent, title: str, subtitle: str = "") -> tk.Frame:
        head = tk.Frame(parent, bg=parent.cget("bg"))
        head.pack(fill="x", pady=(0, 16))
        tk.Label(head, text=title, bg=parent.cget("bg"), fg=NAV_TEXT_HI, font=(FONT, 16, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(
                head, text=subtitle, bg=parent.cget("bg"), fg=MUTED,
                font=FONT_SMALL, wraplength=640, justify="left",
            ).pack(anchor="w", pady=(6, 0))
        return head

    def _vip_btn(self, parent, text: str, command=None) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=VIP_GOLD, fg="#1a1400", activebackground="#d9a020",
            activeforeground="#1a1400", relief="flat",
            font=(FONT, 10, "bold"), padx=16, pady=7, cursor="hand2", bd=0,
        )

    def _accent_btn(self, parent, text: str, command=None, small: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=ACCENT, fg="#ffffff", activebackground=ACCENT_DARK, activeforeground="#ffffff",
            relief="flat", font=(FONT, 9 if small else 10, "bold"),
            padx=14 if not small else 10, pady=5 if not small else 3,
            cursor="hand2", bd=0, highlightthickness=0,
        )

    def _ghost_btn(self, parent, text: str, command=None) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command,
            bg=SURFACE, fg=NAV_TEXT_HI, activebackground=SURFACE_HI,
            relief="flat", font=FONT_SMALL, padx=10, pady=5,
            cursor="hand2", bd=0, highlightthickness=1, highlightbackground=BORDER,
        )

    def _style_nav_item(self, nav_key: str) -> None:
        for key, (row, lbl, bar) in self._nav_buttons.items():
            active = key == nav_key
            bg = NAV_ACTIVE_BG if active else SIDEBAR_BG
            row.config(bg=bg)
            bar.config(bg=ACCENT if active else bg)
            lbl.config(
                bg=bg,
                fg=NAV_TEXT_HI if active else NAV_TEXT,
                font=(FONT, 10, "bold" if active else "normal"),
            )

    def _build_layout(self) -> None:
        self.main_body = tk.Frame(self, bg=BG)
        self.main_body.pack(fill="both", expand=True)

        body = tk.Frame(self.main_body, bg=BG)
        body.pack(fill="both", expand=True)

        # ── 左侧导航 ──
        self.sidebar = tk.Frame(body, bg=SIDEBAR_BG, width=NAV_WIDTH, highlightthickness=1, highlightbackground=SIDEBAR_BORDER)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo_box = tk.Frame(self.sidebar, bg=SIDEBAR_BG)
        logo_box.pack(fill="x", pady=(22, 18), padx=16)
        mark = tk.Frame(logo_box, bg=ACCENT, width=44, height=44, highlightthickness=0)
        mark.pack()
        mark.pack_propagate(False)
        tk.Label(mark, text="P", bg=ACCENT, fg="#ffffff", font=(FONT, 20, "bold")).place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(logo_box, text="PLAYGAME", bg=SIDEBAR_BG, fg=NAV_TEXT_HI, font=(FONT, 12, "bold")).pack(pady=(10, 0))
        tk.Label(logo_box, text="每天持续更新", bg=SIDEBAR_BG, fg=SLOGAN_YELLOW, font=(FONT, 9)).pack(pady=(4, 0))

        self._nav_buttons: Dict[str, Tuple[tk.Frame, tk.Label, tk.Frame]] = {}
        self._add_nav_section("商城", [
            ("home", "首页"),
            ("catalog", "Steam 入库"),
            ("online", "Steam 联机"),
            ("battle", "联机对战"),
            ("single", "单机游戏"),
            ("switch", "Switch"),
            ("retro", "怀旧游戏"),
            ("mobile", "手机游戏"),
        ])
        self._add_nav_section("充值", [("cdk", "兑换会员")])
        self._add_nav_section("我的", [
            ("mine", "我的游戏库"),
            ("favorites", "我的收藏"),
            ("account", "账户中心"),
            ("settings", "系统设置"),
        ])
        tk.Label(self.sidebar, text="", bg=SIDEBAR_BG).pack(fill="both", expand=True)

        # ── 右侧主区域 ──
        self.right_panel = tk.Frame(body, bg=CONTENT_BG)
        self.right_panel.pack(side="left", fill="both", expand=True)

        self.topbar = tk.Frame(self.right_panel, bg=TOPBAR_BG, height=TOPBAR_H, highlightthickness=0)
        self.topbar.pack(fill="x")
        self.topbar.pack_propagate(False)

        tk.Frame(self.topbar, bg=SIDEBAR_BORDER, width=1).pack(side="left", fill="y")

        nav_btns = tk.Frame(self.topbar, bg=TOPBAR_BG)
        nav_btns.pack(side="left", padx=(14, 8))
        for sym, cmd in (("‹", None), ("›", None), ("↻", lambda: self._reload_view())):
            b = tk.Label(nav_btns, text=sym, bg=TOPBAR_BG, fg=NAV_TEXT, font=(FONT, 14), cursor="hand2")
            b.pack(side="left", padx=6, pady=14)
            if cmd:
                b.bind("<Button-1>", lambda _e, c=cmd: c())
                b.bind("<Enter>", lambda _e, w=b: w.config(fg=NAV_TEXT_HI))
                b.bind("<Leave>", lambda _e, w=b: w.config(fg=NAV_TEXT))

        search_outer = tk.Frame(self.topbar, bg=TOPBAR_BG)
        search_outer.pack(side="left", fill="x", expand=True, padx=(4, 16), pady=10)

        search_wrap = tk.Frame(search_outer, bg=SEARCH_BG, highlightthickness=1, highlightbackground=BORDER)
        search_wrap.pack(fill="both", expand=True)

        self.search_var = tk.StringVar()
        entry = tk.Entry(
            search_wrap, textvariable=self.search_var, font=FONT_BODY,
            relief="flat", bg=SEARCH_BG, fg=NAV_TEXT_HI,
            insertbackground=ACCENT_GLOW, highlightthickness=0, bd=0,
        )
        entry.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=9)
        entry.bind("<Return>", lambda _e: self.on_search())
        entry.bind("<FocusIn>", lambda _e: search_wrap.config(highlightbackground=ACCENT))
        entry.bind("<FocusOut>", lambda _e: search_wrap.config(highlightbackground=BORDER))
        self._search_placeholder = "搜索游戏名称或 AppID"

        btn_row = tk.Frame(search_wrap, bg=SEARCH_BG)
        btn_row.pack(side="right", padx=6, pady=5)
        self._accent_btn(btn_row, "搜索", self.on_search, small=True).pack(side="left", padx=(0, 4))
        self._ghost_btn(btn_row, "模糊", self.on_fuzzy_search).pack(side="left")

        right_top = tk.Frame(self.topbar, bg=TOPBAR_BG)
        right_top.pack(side="right", padx=16)

        self.status_label = tk.Label(right_top, text="", bg=TOPBAR_BG, fg=MUTED, font=FONT_SMALL)
        self.status_label.pack(side="right", padx=(10, 0))

        cs_link = tk.Label(right_top, text="客服", bg=TOPBAR_BG, fg=NAV_TEXT, font=FONT_SMALL, cursor="hand2")
        cs_link.pack(side="right", padx=10)
        cs_link.bind("<Enter>", lambda _e: cs_link.config(fg=ACCENT_GLOW))
        cs_link.bind("<Leave>", lambda _e: cs_link.config(fg=NAV_TEXT))

        self.user_vip_label = tk.Label(right_top, text="", bg=TOPBAR_BG, fg=VIP_GOLD, font=FONT_SMALL)
        self.user_vip_label.pack(side="right", padx=(6, 0))

        self.login_btn = self._accent_btn(right_top, "登录 / 注册", self._on_login_click, small=True)
        self.login_btn.pack(side="right")

        # 首页专属：推荐轮播 + 统计 + 分类 Tab
        self.home_header = tk.Frame(self.right_panel, bg=CONTENT_BG, padx=20, pady=12)

        # 网格页工具条
        self.sub_toolbar = tk.Frame(self.right_panel, bg=CONTENT_BG, padx=20, pady=8)

        self.list_info_label = tk.Label(self.sub_toolbar, text="", bg=CONTENT_BG, fg=MUTED, font=FONT_SMALL)
        self.list_info_label.pack(side="left")

        self.selected_label = tk.Label(
            self.sub_toolbar, text="单击选择 · 双击入库", bg=CONTENT_BG, fg=MUTED, font=FONT_SMALL,
        )
        self.selected_label.pack(side="left", padx=(14, 0))

        self.import_btn = self._accent_btn(self.sub_toolbar, "一键入库", self.on_import, small=True)
        self.import_btn.pack(side="right", padx=(6, 0))

        self.import_all_btn = self._ghost_btn(self.sub_toolbar, "入库全部", self.on_import_all)
        self.import_all_btn.pack(side="right", padx=(6, 0))

        self.source_combo = ttk.Combobox(self.sub_toolbar, state="readonly", width=22)
        tk.Label(self.sub_toolbar, text="清单源", bg=CONTENT_BG, fg=MUTED, font=FONT_SMALL).pack(side="right", padx=(12, 4))
        self.source_combo.pack(side="right")

        pager = tk.Frame(self.sub_toolbar, bg=CONTENT_BG)
        pager.pack(side="right", padx=10)
        self._ghost_btn(pager, "›", lambda: self._change_page(1)).pack(side="right")
        self.page_label = tk.Label(pager, text="1 / 1", bg=CONTENT_BG, fg=NAV_TEXT, font=FONT_SMALL)
        self.page_label.pack(side="right", padx=8)
        self._ghost_btn(pager, "‹", lambda: self._change_page(-1)).pack(side="right")

        self._build_home_header(self.home_header)

        # 可滚动内容区
        self.content_wrap = tk.Frame(self.right_panel, bg=CONTENT_BG)
        self.content_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        self.canvas = tk.Canvas(self.content_wrap, bg=CONTENT_BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.content_wrap, orient="vertical", command=self.canvas.yview)
        self.scroll_inner = tk.Frame(self.canvas, bg=CONTENT_BG)
        self.grid_frame = tk.Frame(self.scroll_inner, bg=CONTENT_BG)

        self.scroll_inner.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._canvas_window = self.canvas.create_window((0, 0), window=self.scroll_inner, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.grid_frame.pack(fill="x")

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.settings_frame = tk.Frame(self.right_panel, bg=CONTENT_BG, padx=24, pady=12)
        self._build_settings_panel()

        self.cdk_frame = tk.Frame(self.right_panel, bg=CONTENT_BG, padx=24, pady=12)
        self._build_cdk_panel()

        self.account_frame = tk.Frame(self.right_panel, bg=CONTENT_BG, padx=24, pady=12)
        self._build_account_panel()

        # 右下角浮动客服
        self.float_cs = tk.Canvas(
            self.main_body, width=48, height=48, bg=CONTENT_BG,
            highlightthickness=0, cursor="hand2",
        )
        self.float_cs.create_oval(2, 2, 46, 46, fill=ACCENT, outline=ACCENT_GLOW)
        self.float_cs.create_text(24, 24, text="客", fill="#ffffff", font=(FONT, 11, "bold"))
        self.float_cs.place(relx=1.0, rely=1.0, x=-20, y=-20, anchor="se")

        self.busy_overlay = tk.Frame(self, bg=BG)
        busy_inner = tk.Frame(self.busy_overlay, bg=SURFACE, padx=28, pady=22, highlightthickness=1, highlightbackground=BORDER)
        busy_inner.place(relx=0.5, rely=0.5, anchor="center")
        self.busy_label = tk.Label(busy_inner, text="请稍候…", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SECTION)
        self.busy_label.pack()
        self.busy_overlay.place_forget()

        self._layout_ready = True
        self.main_body.pack_forget()

    def _build_home_header(self, parent: tk.Frame) -> None:
        title_row = tk.Frame(parent, bg=CONTENT_BG)
        title_row.pack(fill="x", pady=(0, 10))
        tk.Label(title_row, text="推荐游戏", bg=CONTENT_BG, fg=NAV_TEXT_HI, font=FONT_TITLE).pack(side="left")
        tk.Label(title_row, text="查看全部", bg=CONTENT_BG, fg=ACCENT_GLOW, font=FONT_SMALL, cursor="hand2").pack(side="right")
        daily_tag = tk.Frame(title_row, bg=NAV_ACTIVE_BG, highlightthickness=1, highlightbackground=BORDER)
        daily_tag.pack(side="right", padx=(0, 12))
        tk.Label(daily_tag, text="每日更新", bg=NAV_ACTIVE_BG, fg=ACCENT_GLOW, font=FONT_SMALL).pack(padx=8, pady=3)

        hero_body = tk.Frame(
            parent, bg=SURFACE, height=HERO_H,
            highlightthickness=1, highlightbackground=BORDER,
        )
        hero_body.pack(fill="x", pady=(0, 12))
        hero_body.pack_propagate(False)

        self.hero_banner = tk.Label(hero_body, text="", bg=SURFACE_HI, fg=MUTED, font=FONT_BODY, anchor="center")
        self.hero_banner.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)

        list_wrap = tk.Frame(hero_body, bg=SURFACE, width=216, highlightthickness=1, highlightbackground=BORDER)
        list_wrap.pack(side="right", fill="y")
        list_wrap.pack_propagate(False)

        self._hero_list_labels.clear()
        self._hero_list_rows.clear()
        for appid, name in DEV_HERO_GAMES:
            row = tk.Frame(list_wrap, bg=SURFACE)
            row.pack(fill="x", padx=6, pady=3)
            bar = tk.Frame(row, bg=SURFACE, width=3)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(
                row, text=name, bg=SURFACE, fg=NAV_TEXT,
                font=FONT_BODY, anchor="w", cursor="hand2",
            )
            lbl.pack(side="left", fill="x", expand=True, padx=10, pady=10)
            for w in (row, lbl, bar):
                w.bind("<Button-1>", lambda _e, a=appid: self._select_hero(a))
            self._hero_list_labels.append(lbl)
            self._hero_list_rows.append((row, bar))

        self.stats_bar = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        self.stats_bar.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(self.stats_bar, bg=SURFACE)
        inner.pack(fill="x", padx=14, pady=10)
        tk.Label(inner, text="105,216 款游戏", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SMALL).pack(side="left")
        tk.Label(inner, text="·", bg=SURFACE, fg=BORDER, font=FONT_SMALL).pack(side="left", padx=8)
        tk.Label(inner, text="昨日 +79", bg=SURFACE, fg=MUTED, font=FONT_SMALL).pack(side="left")
        tk.Label(inner, text="在线 758,433", bg=SURFACE, fg=SUCCESS, font=FONT_SMALL).pack(side="right")
        tk.Label(inner, text="游戏中 689,318", bg=SURFACE, fg=ACCENT_GLOW, font=FONT_SMALL).pack(side="right", padx=(0, 16))

        tab_row = tk.Frame(parent, bg=CONTENT_BG)
        tab_row.pack(fill="x", pady=(0, 4))
        self._cat_tab_labels: Dict[str, tk.Label] = {}
        self._cat_tab_pills: Dict[str, tk.Frame] = {}
        for cat in CATEGORIES:
            pill = tk.Frame(tab_row, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
            pill.pack(side="left", padx=(0, 8), pady=2)
            lbl = tk.Label(pill, text=cat, bg=SURFACE, fg=MUTED, font=FONT_SMALL, cursor="hand2")
            lbl.pack(padx=12, pady=6)
            lbl.bind("<Button-1>", lambda _e, c=cat: self._on_category_tab(c))
            pill.bind("<Button-1>", lambda _e, c=cat: self._on_category_tab(c))
            self._cat_tab_labels[cat] = lbl
            self._cat_tab_pills[cat] = pill
        self._style_category_tab("热门推荐")

    def _select_hero(self, appid: str) -> None:
        self._hero_appid = appid
        for i, (aid, _name) in enumerate(DEV_HERO_GAMES):
            lbl = self._hero_list_labels[i]
            row, bar = self._hero_list_rows[i]
            active = aid == appid
            bg = NAV_ACTIVE_BG if active else SURFACE
            row.config(bg=bg)
            bar.config(bg=ACCENT if active else bg)
            lbl.config(bg=bg, fg=NAV_TEXT_HI if active else NAV_TEXT, font=(FONT, 10, "bold" if active else "normal"))
        photo = self._hero_photos.get(appid)
        if photo:
            self.hero_banner.config(image=photo, text="")
            self.hero_banner.image = photo
        else:
            self.runner.submit(self._load_hero_photo(appid)).add_done_callback(
                lambda f: self.after(0, lambda: self._apply_hero_photo(f, appid))
            )

    async def _load_hero_photo(self, appid: str):
        return await self.service.catalog.load_photo(appid, None)

    def _apply_hero_photo(self, future, appid: str) -> None:
        try:
            photo = future.result()
        except Exception:
            return
        if not photo:
            return
        self._hero_photos[appid] = photo
        if self._hero_appid == appid:
            self.hero_banner.config(image=photo, text="")
            self.hero_banner.image = photo

    def _load_all_hero_covers(self) -> None:
        for appid, _ in DEV_HERO_GAMES:
            self.runner.submit(self._load_hero_photo(appid)).add_done_callback(
                lambda f, a=appid: self.after(0, lambda: self._apply_hero_photo(f, a))
            )
        self._select_hero(self._hero_appid)

    def _style_category_tab(self, name: str) -> None:
        self._active_category = name
        for cat, lbl in self._cat_tab_labels.items():
            active = cat == name
            pill = self._cat_tab_pills[cat]
            bg = NAV_ACTIVE_BG if active else SURFACE
            pill.config(bg=bg, highlightbackground=ACCENT if active else BORDER)
            lbl.config(bg=bg, fg=NAV_TEXT_HI if active else MUTED, font=(FONT, 10, "bold" if active else "normal"))

    def _on_category_tab(self, name: str) -> None:
        self._style_category_tab(name)
        if not self._layout_ready:
            return
        if self.current_view.get() in ("home", "catalog"):
            self._reload_view()

    def on_fuzzy_search(self) -> None:
        self.on_search()

    def _on_login_click(self) -> None:
        if self.current_user:
            self._switch_view("account")
        else:
            self.main_body.pack_forget()
            self.auth_overlay.pack(fill="both", expand=True)

    def _add_nav_section(self, title: str, items: List[Tuple[str, str]]) -> None:
        tk.Label(
            self.sidebar, text=title.upper(), bg=SIDEBAR_BG, fg=MUTED,
            font=(FONT, 8, "bold"), anchor="w",
        ).pack(anchor="w", padx=20, pady=(16, 6))
        for key, label in items:
            row = tk.Frame(self.sidebar, bg=SIDEBAR_BG)
            row.pack(fill="x", padx=10, pady=1)
            bar = tk.Frame(row, bg=SIDEBAR_BG, width=3)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(
                row, text=f"  {label}", bg=SIDEBAR_BG, fg=NAV_TEXT,
                font=FONT_BODY, anchor="w", cursor="hand2",
            )
            lbl.pack(side="left", fill="x", expand=True, padx=4, pady=8)
            for w in (row, bar, lbl):
                w.bind("<Button-1>", lambda _e, k=key: self._switch_view(k))
                w.bind("<Enter>", lambda _e, k=key, r=row, l=lbl, b=bar: self._nav_hover(k, r, l, b, True))
                w.bind("<Leave>", lambda _e, k=key, r=row, l=lbl, b=bar: self._nav_hover(k, r, l, b, False))
            self._nav_buttons[key] = (row, lbl, bar)

    def _nav_hover(self, key: str, row: tk.Frame, lbl: tk.Label, bar: tk.Frame, enter: bool) -> None:
        if key == self._active_nav:
            return
        bg = NAV_HOVER if enter else SIDEBAR_BG
        row.config(bg=bg)
        bar.config(bg=bg)
        lbl.config(bg=bg, fg=NAV_TEXT_HI if enter else NAV_TEXT)

    def _build_auth_overlay(self) -> None:
        self.auth_overlay = tk.Frame(self, bg=BG)
        self.auth_overlay.pack(fill="both", expand=True)

        center = tk.Frame(self.auth_overlay, bg=BG)
        center.place(relx=0.5, rely=0.48, anchor="center")

        card = tk.Frame(
            center, bg=AUTH_CARD_BG, padx=40, pady=36,
            highlightthickness=1, highlightbackground=BORDER,
        )
        card.pack()

        brand = tk.Frame(card, bg=AUTH_CARD_BG)
        brand.pack(fill="x", pady=(0, 22))
        mark = tk.Frame(brand, bg=ACCENT, width=40, height=40)
        mark.pack(side="left")
        mark.pack_propagate(False)
        tk.Label(mark, text="P", bg=ACCENT, fg="#fff", font=(FONT, 16, "bold")).place(relx=0.5, rely=0.5, anchor="center")
        txt = tk.Frame(brand, bg=AUTH_CARD_BG)
        txt.pack(side="left", padx=(12, 0))
        tk.Label(txt, text="PLAYGAME", bg=AUTH_CARD_BG, fg=NAV_TEXT_HI, font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(
            txt, text="登录后浏览游戏、兑换 VIP、入库到 Steam",
            bg=AUTH_CARD_BG, fg=MUTED, font=FONT_SMALL,
        ).pack(anchor="w", pady=(4, 0))

        tab_bar = tk.Frame(card, bg=AUTH_CARD_BG)
        tab_bar.pack(fill="x", pady=(0, 18))
        self._auth_mode = tk.StringVar(value="login")

        def _set_tab(mode: str) -> None:
            self._auth_mode.set(mode)
            login_tab.config(
                fg=NAV_TEXT_HI if mode == "login" else MUTED,
                bg=NAV_ACTIVE_BG if mode == "login" else SURFACE_HI,
            )
            reg_tab.config(
                fg=NAV_TEXT_HI if mode == "register" else MUTED,
                bg=NAV_ACTIVE_BG if mode == "register" else SURFACE_HI,
            )
            self.auth_reg_name_row.pack_forget()
            if mode == "register":
                self.auth_reg_name_row.pack(fill="x", pady=(0, 10), before=self.auth_user_row)

        login_tab = tk.Label(
            tab_bar, text="  登录  ", bg=NAV_ACTIVE_BG, fg=NAV_TEXT_HI,
            font=(FONT, 10, "bold"), cursor="hand2",
        )
        login_tab.pack(side="left", padx=(0, 8))
        login_tab.bind("<Button-1>", lambda _e: _set_tab("login"))
        reg_tab = tk.Label(
            tab_bar, text="  注册  ", bg=SURFACE_HI, fg=MUTED,
            font=(FONT, 10), cursor="hand2",
        )
        reg_tab.pack(side="left")
        reg_tab.bind("<Button-1>", lambda _e: _set_tab("register"))

        form = tk.Frame(card, bg=AUTH_CARD_BG)
        form.pack(fill="x")

        self.auth_reg_name_row = tk.Frame(form, bg=AUTH_CARD_BG)
        self._form_label(self.auth_reg_name_row, "昵称").pack(side="left")
        self.auth_display_var = tk.StringVar()
        self._dark_entry(self.auth_reg_name_row, self.auth_display_var).pack(
            side="left", fill="x", expand=True, ipady=7, padx=(8, 0),
        )

        self.auth_user_row = tk.Frame(form, bg=AUTH_CARD_BG)
        self.auth_user_row.pack(fill="x", pady=(0, 10))
        self._form_label(self.auth_user_row, "用户名").pack(side="left")
        self.auth_user_var = tk.StringVar()
        self._dark_entry(self.auth_user_row, self.auth_user_var).pack(
            side="left", fill="x", expand=True, ipady=7, padx=(8, 0),
        )

        pw_row = tk.Frame(form, bg=AUTH_CARD_BG)
        pw_row.pack(fill="x", pady=(0, 16))
        self._form_label(pw_row, "密码").pack(side="left")
        self.auth_pass_var = tk.StringVar()
        pw_entry = self._dark_entry(pw_row, self.auth_pass_var, show="•")
        pw_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(8, 0))
        pw_entry.bind("<Return>", lambda _e: self._submit_auth())

        self.auth_msg_label = tk.Label(
            card, text="", bg=AUTH_CARD_BG, fg=DANGER,
            font=FONT_SMALL, wraplength=340, justify="left",
        )
        self.auth_msg_label.pack(fill="x", pady=(0, 10))

        self._accent_btn(card, "进入 PLAYGAME", self._submit_auth).pack(fill="x", ipady=4)

        tk.Label(
            card, text="VIP 可使用入库与 CDK 激活；激活码在登录后于「账户」页兑换",
            bg=AUTH_CARD_BG, fg=MUTED, font=(FONT, 8), wraplength=340, justify="left",
        ).pack(anchor="w", pady=(16, 0))
        self.auth_server_hint = tk.Label(
            card, text="", bg=AUTH_CARD_BG, fg=ACCENT_GLOW,
            font=(FONT, 8), wraplength=340, justify="left",
        )
        self.auth_server_hint.pack(anchor="w", pady=(8, 0))

    def _setup_auth_from_config(self, config: dict) -> None:
        url = str(config.get("Box_Server_URL", "")).strip().rstrip("/")
        if url == self._box_server_url:
            return
        self._box_server_url = url
        if url:
            from box_remote_auth import RemoteBoxAuthService

            self.auth = RemoteBoxAuthService(url)
            if hasattr(self, "auth_server_hint"):
                self.auth_server_hint.config(text=f"已连接线上账号：{url}")
        else:
            self.auth = ClientAuthService()
            if hasattr(self, "auth_server_hint"):
                self.auth_server_hint.config(text="未配置 Box_Server_URL，使用本机账号（仅本电脑）")

    def _build_account_panel(self) -> None:
        self._page_header(
            self.account_frame,
            "账户中心",
            "管理 VIP 会员。入库、批量入库、CDK 激活入库均需 VIP 权限。",
        )

        profile = self._surface_card(self.account_frame, padx=20, pady=18)
        profile.pack(fill="x", pady=(0, 14))

        avatar = tk.Frame(profile, bg=ACCENT, width=52, height=52)
        avatar.pack(side="left")
        avatar.pack_propagate(False)
        self.account_avatar_label = tk.Label(avatar, text="?", bg=ACCENT, fg="#fff", font=(FONT, 18, "bold"))
        self.account_avatar_label.place(relx=0.5, rely=0.5, anchor="center")

        info = tk.Frame(profile, bg=SURFACE)
        info.pack(side="left", fill="x", expand=True, padx=(16, 0))
        self.account_name_label = tk.Label(
            info, text="用户：-", bg=SURFACE, fg=NAV_TEXT_HI,
            font=(FONT, 11, "bold"), anchor="w",
        )
        self.account_name_label.pack(fill="x")
        self.account_vip_label = tk.Label(
            info, text="VIP：未开通", bg=SURFACE, fg=MUTED, font=FONT_SMALL, anchor="w",
        )
        self.account_vip_label.pack(fill="x", pady=(6, 0))

        act_row = tk.Frame(profile, bg=SURFACE)
        act_row.pack(side="right")
        self._ghost_btn(act_row, "退出登录", self.on_logout).pack(side="left")

        vip_card = self._surface_card(self.account_frame, padx=20, pady=18)
        vip_card.pack(fill="x")
        tk.Label(vip_card, text="VIP 激活码", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SECTION).pack(anchor="w")
        tk.Label(
            vip_card, text="输入代理发放的 VIP 码以开通入库权限",
            bg=SURFACE, fg=MUTED, font=FONT_SMALL,
        ).pack(anchor="w", pady=(4, 12))
        row = tk.Frame(vip_card, bg=SURFACE)
        row.pack(fill="x")
        self.vip_code_var = tk.StringVar()
        self._dark_entry(row, self.vip_code_var, font=("Consolas", 12)).pack(
            side="left", fill="x", expand=True, ipady=9, padx=(0, 12),
        )
        self._vip_btn(row, "激活 VIP", self.on_activate_vip).pack(side="left")

    def _try_auto_login(self) -> None:
        token = self.auth.load_session_token()
        if token:
            user = self.auth.verify_token(token)
            if user:
                self._on_auth_success(user, token)
                return
        self.auth_overlay.pack(fill="both", expand=True)

    def _submit_auth(self) -> None:
        username = self.auth_user_var.get().strip()
        password = self.auth_pass_var.get()
        if not username or not password:
            self.auth_msg_label.config(text="请填写用户名和密码")
            return
        try:
            if self._auth_mode.get() == "register":
                self.auth.register(username, password, self.auth_display_var.get().strip())
                result = self.auth.login(username, password)
                self._on_auth_success(result["user"], result["token"])
            else:
                result = self.auth.login(username, password)
                self._on_auth_success(result["user"], result["token"])
        except ValueError as e:
            self.auth_msg_label.config(text=str(e))
        except Exception as e:
            self.auth_msg_label.config(text=f"操作失败：{e}")

    def _on_auth_success(self, user: dict, token: str) -> None:
        self.current_user = user
        self._session_token = token
        self.auth_overlay.pack_forget()
        self.main_body.pack(fill="both", expand=True)
        self._update_user_panel()
        if not self._bootstrap_done:
            self._bootstrap()

    def _update_user_panel(self) -> None:
        user = self.current_user or {}
        name = user.get("display_name") or user.get("username") or "用户"
        if hasattr(self, "login_btn"):
            if self.current_user:
                short = name if len(name) <= 10 else name[:9] + "…"
                self.login_btn.config(text=short, command=self._on_login_click)
            else:
                self.login_btn.config(text="登录 / 注册", command=self._on_login_click)
        if self._user_is_vip():
            exp = user.get("vip_expires_at") or ""
            vip_text = "VIP" + (f" · {exp}" if exp else " · 永久")
            self.user_vip_label.config(text=vip_text, fg=VIP_GOLD)
        else:
            self.user_vip_label.config(text="" if not self.current_user else "普通用户", fg=NAV_TEXT)
        if hasattr(self, "account_avatar_label"):
            initial = (name.strip()[:1] or "?").upper()
            self.account_avatar_label.config(text=initial)
        if hasattr(self, "account_name_label"):
            self.account_name_label.config(text=f"用户：{name}")
            if self._user_is_vip():
                exp = user.get("vip_expires_at") or ""
                self.account_vip_label.config(
                    text="VIP：已开通" + (f"（有效期至 {exp}）" if exp else "（永久）"),
                    fg=VIP_GOLD,
                )
            else:
                self.account_vip_label.config(text="VIP：未开通 · 请在下方输入激活码", fg=MUTED)

    def _user_is_vip(self) -> bool:
        if not self.current_user:
            return False
        uid = str(self.current_user.get("id", ""))
        if uid and self.auth.is_vip(uid):
            refreshed = self.auth.verify_token(self._session_token)
            if refreshed:
                self.current_user = refreshed
            return True
        return bool(self.current_user.get("vip"))

    def _require_vip(self, action: str = "入库") -> bool:
        if self._user_is_vip():
            return True
        if messagebox.askyesno("需要 VIP", f"{action}功能仅限 VIP 用户。\n\n是否前往「账户」页激活 VIP？"):
            self._switch_view("account")
        return False

    def on_activate_vip(self) -> None:
        if not self.current_user:
            messagebox.showwarning("提示", "请先登录")
            return
        code = self.vip_code_var.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入 VIP 激活码")
            return

        async def _ensure_config():
            if not self._config:
                self._config = await self.service.load_config()
            return self.auth.activate_vip(str(self.current_user["id"]), code, self._config)

        def _done(future) -> None:
            try:
                user = future.result()
                self.current_user = user
                self.vip_code_var.set("")
                self._update_user_panel()
                self._reload_view()
                messagebox.showinfo("激活成功", "VIP 已开通，现在可以使用入库功能")
            except ValueError as e:
                messagebox.showerror("激活失败", str(e))
            except Exception as e:
                messagebox.showerror("激活失败", str(e))

        self.runner.submit(_ensure_config()).add_done_callback(lambda f: self.after(0, lambda: _done(f)))

    def on_logout(self) -> None:
        if self._session_token:
            self.auth.logout(self._session_token)
        self._session_token = ""
        self.current_user = None
        self._update_user_panel()
        self.main_body.pack_forget()
        self.auth_user_var.set("")
        self.auth_pass_var.set("")
        self.auth_display_var.set("")
        self.auth_msg_label.config(text="")
        self.auth_overlay.pack(fill="both", expand=True)

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfig(self._canvas_window, width=event.width)

    def _switch_view(self, nav_key: str) -> None:
        self._active_nav = nav_key
        actual = VIEW_ALIASES.get(nav_key, nav_key)
        self.current_view.set(actual)

        self._style_nav_item(nav_key)

        self.home_header.pack_forget()
        self.content_wrap.pack_forget()
        self.settings_frame.pack_forget()
        self.cdk_frame.pack_forget()
        self.account_frame.pack_forget()
        self.sub_toolbar.pack_forget()

        grid_views = ("home", "catalog", "mine")

        if actual == "settings":
            self.settings_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        elif actual == "cdk":
            self.cdk_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        elif actual == "account":
            self._update_user_panel()
            self.account_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        elif actual in grid_views:
            if actual == "home":
                self.home_header.pack(fill="x", after=self.topbar)
            else:
                self.sub_toolbar.pack(fill="x", after=self.topbar)
            self.content_wrap.pack(fill="both", expand=True)
            if actual in ("home", "catalog"):
                self.list_page = 1
            self._reload_view()
        else:
            self.content_wrap.pack(fill="both", expand=True)
            self._reload_view()

    def _build_settings_panel(self) -> None:
        self._page_header(
            self.settings_frame,
            "设置",
            "清单源与解锁工具由程序自动检测。入库前可在工具栏选择清单源。",
        )

        form = self._surface_card(self.settings_frame, padx=20, pady=18)
        form.pack(fill="x", pady=(0, 12))
        tk.Label(form, text="Steam 路径", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SMALL).grid(row=0, column=0, sticky="w", pady=8)
        self.steam_path_var = tk.StringVar()
        self._dark_entry(form, self.steam_path_var, font=("Consolas", 10)).grid(
            row=0, column=1, sticky="ew", padx=(12, 0), ipady=6,
        )
        form.columnconfigure(1, weight=1)

        opts = self._surface_card(self.settings_frame, padx=20, pady=16)
        opts.pack(fill="x", pady=(0, 12))
        tk.Label(opts, text="入库选项", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SECTION).pack(anchor="w", pady=(0, 10))
        for text, var in (
            ("失败自动换源", self.auto_fallback_var),
            ("入库 DLC", self.add_dlc_var),
            ("自动更新清单", self.auto_update_var),
            ("入库后自动注入并重启 Steam", self.auto_finalize_var),
        ):
            ttk.Checkbutton(opts, text=text, variable=var, style="Dark.TCheckbutton").pack(anchor="w", pady=3)

        net = self._surface_card(self.settings_frame, padx=20, pady=18)
        net.pack(fill="x", pady=(0, 12))
        tk.Label(net, text="线上服务器", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SECTION).pack(anchor="w")
        tk.Label(
            net,
            text="填写 Web 地址后，盒子登录 / VIP 与代理后台一致（与 CDK 同一套账号）",
            bg=SURFACE, fg=MUTED, font=FONT_SMALL, wraplength=560, justify="left",
        ).pack(anchor="w", pady=(6, 12))
        row = tk.Frame(net, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text="Box_Server_URL", bg=SURFACE, fg=MUTED, font=FONT_SMALL, width=14, anchor="w").pack(side="left")
        self.box_server_url_var = tk.StringVar()
        self._dark_entry(row, self.box_server_url_var, font=("Consolas", 10)).pack(
            side="left", fill="x", expand=True, padx=(8, 0), ipady=7,
        )
        self.box_server_status = tk.Label(net, text="", bg=SURFACE, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.box_server_status.pack(fill="x", pady=(10, 0))

        btns = tk.Frame(self.settings_frame, bg=CONTENT_BG)
        btns.pack(fill="x", pady=4)
        self._accent_btn(btns, "保存配置", self.on_save_config).pack(side="left", padx=(0, 8))
        self._ghost_btn(btns, "重新检测环境", self._bootstrap).pack(side="left", padx=(0, 8))
        self._ghost_btn(btns, "修复内置注入", self._run_repair_injector).pack(side="left")

    def _build_cdk_panel(self) -> None:
        self._page_header(
            self.cdk_frame,
            "CDK 激活",
            "流程：部署激活环境 → 输入 CDK → 自动入库并重启 Steam（需 VIP）",
        )

        steps = tk.Frame(self.cdk_frame, bg=CONTENT_BG)
        steps.pack(fill="x", pady=(0, 14))
        for i, label in enumerate(("① 部署环境", "② 输入 CDK", "③ 自动入库"), start=1):
            pill = tk.Frame(steps, bg=NAV_ACTIVE_BG, highlightthickness=1, highlightbackground=BORDER)
            pill.pack(side="left", padx=(0, 10))
            tk.Label(pill, text=label, bg=NAV_ACTIVE_BG, fg=ACCENT_GLOW, font=FONT_SMALL).pack(padx=12, pady=6)

        hero = self._surface_card(self.cdk_frame, padx=22, pady=20)
        hero.pack(fill="x")

        row = tk.Frame(hero, bg=SURFACE)
        row.pack(fill="x", pady=(0, 12))
        self.cdk_var = tk.StringVar()
        cdk_entry = self._dark_entry(row, self.cdk_var, font=("Consolas", 14))
        cdk_entry.pack(side="left", fill="x", expand=True, ipady=11, padx=(0, 12))
        cdk_entry.bind("<Return>", lambda _e: self.on_activate_cdk())
        self._accent_btn(row, "激活 CDK", self.on_activate_cdk).pack(side="left", ipady=6)

        self.cdk_status_label = tk.Label(
            hero, text="", bg=SURFACE, fg=MUTED, font=FONT_SMALL, anchor="w", wraplength=560, justify="left",
        )
        self.cdk_status_label.pack(fill="x", pady=(0, 12))

        btns = tk.Frame(hero, bg=SURFACE)
        btns.pack(fill="x")
        self._ghost_btn(btns, "1. 部署激活环境", self.on_prepare_cdk_env).pack(side="left", padx=(0, 8))
        self._ghost_btn(btns, "打开 Steam 兑换窗口", self._open_steam_activate).pack(side="left")

        demo = self._surface_card(self.cdk_frame, padx=18, pady=14)
        demo.pack(fill="x", pady=(14, 0))
        tk.Label(demo, text="提示", bg=SURFACE, fg=NAV_TEXT_HI, font=FONT_SECTION).pack(anchor="w")
        tk.Label(
            demo,
            text="演示 CDK：DEMO-7300-CSGO-0001 · DEMO-5700-DOTA-0002 · DEMO-1056-TERR-0003",
            bg=SURFACE, fg=MUTED, font=FONT_SMALL,
        ).pack(anchor="w", pady=(8, 4))
        tk.Label(
            demo, text="生成新 CDK：python gen_cdk.py 730 --count 5",
            bg=SURFACE, fg=MUTED, font=FONT_SMALL,
        ).pack(anchor="w")

    def on_prepare_cdk_env(self) -> None:
        if self._ui_dev:
            self._ui_dev_notice("部署激活环境")
            return
        self._set_cdk_status("正在部署激活环境…")
        self.set_busy(True, "部署激活环境…")
        self.runner.submit(self.service.prepare_activation_environment()).add_done_callback(
            self._on_prepare_cdk_done
        )

    def _on_prepare_cdk_done(self, future) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                ok, msg = future.result()
            except Exception as e:
                messagebox.showerror("部署失败", str(e))
                return
            if ok:
                self._set_cdk_status(msg, ok=True)
                messagebox.showinfo("环境就绪", msg)
                self.service.open_steam_activate_window()
            else:
                self._set_cdk_status(msg, err=True)
                messagebox.showerror("部署失败", msg)

        self.after(0, _apply)

    def _open_steam_activate(self) -> None:
        if self.service.open_steam_activate_window():
            messagebox.showinfo("提示", "已尝试打开 Steam CDK 兑换窗口")
        else:
            messagebox.showwarning("提示", "无法打开 Steam，请先配置 Steam 路径")

    def on_activate_cdk(self) -> None:
        if not self._require_vip("CDK 激活入库"):
            return
        cdk = self.cdk_var.get().strip()
        if not cdk:
            messagebox.showwarning("提示", "请输入 CDK 激活码")
            return
        source = self._default_import_source()
        if not source:
            self._ensure_manifest_sources()
            source = self._default_import_source()
        if not source:
            return
        if not messagebox.askyesno("确认激活", f"CDK: {cdk}\n清单源: {source.name}\n\n将自动入库并重启 Steam"):
            return

        self._set_cdk_status(f"正在激活 · 清单源 {source.name}…")
        options = self._current_options()
        github_repo = source.repo if source.kind in ("builtin_github", "custom_github") else None
        self.set_busy(True, "CDK 激活中…")
        coro = self.service.activate_cdk(
            cdk,
            source,
            options,
            auto_fallback=self.auto_fallback_var.get(),
            github_repo=github_repo,
            auto_finalize=self.auto_finalize_var.get(),
            open_steam_ui=True,
        )
        self.runner.submit(coro).add_done_callback(self._on_cdk_activate_done)

    def _on_cdk_activate_done(self, future) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                result: CdkActivationResult = future.result()
            except Exception as e:
                messagebox.showerror("激活失败", str(e))
                return
            if result.success:
                if hasattr(self.auth, "grant_vip_after_cdk"):
                    days = int(self._config.get("Box_Vip_Days_Per_CDK", 30) or 30)
                    user = self.auth.grant_vip_after_cdk(days)
                    if user:
                        self.current_user = user
                        self._update_user_panel()
                self._set_cdk_status(
                    f"激活成功 · {result.game_name}（AppID {result.app_id}）", ok=True,
                )
                messagebox.showinfo(
                    "激活成功",
                    f"游戏：{result.game_name}\nAppID：{result.app_id}\n\n{result.message}",
                )
                self.cdk_var.set("")
                self._switch_view("mine")
            else:
                self._set_cdk_status(result.message, err=True)
                messagebox.showerror("激活失败", result.message)

        self.after(0, _apply)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _change_page(self, delta: int) -> None:
        view = self.current_view.get()
        if view not in ("home", "catalog"):
            return
        next_page = self.list_page + delta
        if next_page < 1 or next_page > self.list_total_pages:
            return
        self.list_page = next_page
        self._reload_view()

    def _ensure_manifest_sources(self) -> bool:
        if not self.manifest_sources and self._bootstrap_done:
            self.manifest_sources = self.service.get_manifest_sources()
            if self.manifest_sources:
                self.source_combo["values"] = [s.name for s in self.manifest_sources]
                self.source_combo.current(0)
        if not self.manifest_sources:
            if not self._bootstrap_done:
                messagebox.showinfo("提示", "程序正在初始化，请稍候再试")
            else:
                messagebox.showwarning("提示", "未检测到可用清单源，请检查网络或 config.json 配置")
            return False
        return True

    def _default_import_source(self) -> Optional[ManifestSource]:
        if not self.manifest_sources:
            return None
        idx = self.source_combo.current()
        if idx >= 0:
            return self.manifest_sources[idx]
        return self.manifest_sources[0]

    def _current_source(self) -> Optional[ManifestSource]:
        return self._default_import_source()

    def _reload_view(self) -> None:
        if self._ui_dev:
            self._reload_view_dev()
            return
        view = self.current_view.get()
        if view == "settings":
            return
        self.set_busy(True, "加载游戏…")
        query = self.search_var.get().strip()
        if query == getattr(self, "_search_placeholder", ""):
            query = ""

        async def _load():
            if view == "mine":
                entries = self.service.get_installed_games()
                cards = await self.service.entries_to_cards(entries, PAGE_SIZE)
                return cards, len(entries), "我的游戏", {"total_pages": 1, "filtered_count": len(entries)}

            manifest_filter = "all" if view == "catalog" else ""
            hint_prefix = "全部游戏" if view == "catalog" else "可入库游戏"
            cards, total, stats = await self.service.get_catalog_cards(
                query=query,
                page=self.list_page,
                page_size=PAGE_SIZE,
                manifest_filter=manifest_filter,
            )
            hint = (
                f"{hint_prefix} · 第 {stats.get('page', self.list_page)}/"
                f"{stats.get('total_pages', 1)} 页"
            )
            return cards, total, hint, stats

        self.runner.submit(_load()).add_done_callback(self._on_cards_loaded)

    def _on_cards_loaded(self, future) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                cards, total, hint, stats = future.result()
            except Exception as e:
                messagebox.showerror("加载失败", str(e))
                return
            self.cards = cards
            self.list_total = total
            self.list_hint = hint
            self.list_total_pages = int(stats.get("total_pages", 1) or 1)
            self.list_page = int(stats.get("page", self.list_page) or self.list_page)
            self._render_grid(cards)
            filtered = stats.get("filtered_count", total)
            manifest_n = stats.get("manifest_count", "")
            extra = f" · 清单库 {manifest_n}" if manifest_n != "" else ""
            self.list_info_label.config(
                text=f"{hint} · 共 {filtered} 款 · 本页 {len(cards)} 款{extra}"
            )
            if hasattr(self, "page_label"):
                self.page_label.config(text=f"{self.list_page} / {self.list_total_pages}")

        self.after(0, _apply)

    def _render_grid(self, cards: List[GameCardInfo]) -> None:
        if not self._layout_ready or not hasattr(self, "grid_frame"):
            return
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self._photos.clear()
        self._card_widgets.clear()

        if not cards:
            empty = tk.Frame(self.grid_frame, bg=CONTENT_BG)
            empty.pack(pady=48)
            tk.Label(empty, text="暂无游戏", bg=CONTENT_BG, fg=NAV_TEXT_HI, font=FONT_SECTION).pack()
            tk.Label(
                empty, text="请尝试其他关键词，或切换到「Steam 入库」浏览全部",
                bg=CONTENT_BG, fg=MUTED, font=FONT_SMALL,
            ).pack(pady=(8, 0))
            return

        vip_ok = self._user_is_vip()
        allow_probe = vip_ok and self.auto_fallback_var.get()
        for i, card in enumerate(cards):
            row, col = divmod(i, GRID_COLS)
            widget = GameCardWidget(
                self.grid_frame,
                card,
                on_select=self._select_card,
                on_import=self._quick_import,
                import_enabled=vip_ok,
                on_locked=lambda: self._require_vip("入库"),
                allow_probe=allow_probe,
                padx=6,
                pady=8,
            )
            widget.grid(row=row, column=col, sticky="n")
            self._card_widgets[card.appid] = widget

        for c in range(GRID_COLS):
            self.grid_frame.columnconfigure(c, weight=1, uniform="col")

        self.runner.submit(self._load_all_covers(cards)).add_done_callback(self._on_covers_loaded)

    async def _load_all_covers(self, cards: List[GameCardInfo]):
        results = {}
        for card in cards:
            photo = await self.service.catalog.load_photo(card.appid, card.header_url or None)
            if photo:
                results[card.appid] = photo
        return results

    def _on_covers_loaded(self, future) -> None:
        def _apply() -> None:
            try:
                photos = future.result()
            except Exception:
                return
            self._photos.update(photos)
            for w in self.grid_frame.winfo_children():
                if isinstance(w, GameCardWidget) and w.card.appid in photos:
                    w.set_photo(photos[w.card.appid])

        self.after(0, _apply)

    def _select_card(self, app_id: str) -> None:
        self.selected_app_id = app_id
        for aid, widget in self._card_widgets.items():
            widget.set_selected(aid == app_id)
        card = next((c for c in self.cards if c.appid == app_id), None)
        name = card.display_title if card else app_id
        self.selected_label.config(text=f"已选 · {name}")

    def _quick_import(self, app_id: str) -> None:
        self.selected_app_id = app_id
        self.on_import()

    def append_log(self, message: str) -> None:
        pass

    def set_busy(self, busy: bool, text: str = "") -> None:
        if hasattr(self, "status_label"):
            self.status_label.config(text=text if busy else "")
        self.config(cursor="watch" if busy else "")
        if hasattr(self, "busy_overlay"):
            if busy:
                self.busy_label.config(text=text or "请稍候…")
                self.busy_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
                self.busy_overlay.lift()
            else:
                self.busy_overlay.place_forget()

    def _set_cdk_status(self, text: str, *, ok: bool = False, err: bool = False) -> None:
        if not hasattr(self, "cdk_status_label"):
            return
        fg = SUCCESS if ok else (DANGER if err else MUTED)
        self.cdk_status_label.config(text=text, fg=fg)

    def _bootstrap(self) -> None:
        self.set_busy(True, "初始化…")
        self.runner.submit(self._async_bootstrap()).add_done_callback(self._on_bootstrap_done)

    async def _async_bootstrap(self) -> Tuple[EnvironmentInfo, dict]:
        env = await self.service.initialize()
        config = await self.service.load_config()
        return env, config

    def _on_bootstrap_done(self, future) -> None:
        def _apply() -> None:
            try:
                env, config = future.result()
            except Exception as e:
                messagebox.showerror("初始化失败", str(e))
                return
            self._apply_settings(config)
            self._config = config
            self._bootstrap_done = True
            self.manifest_sources = self.service.get_manifest_sources()
            self.source_combo["values"] = [s.name for s in self.manifest_sources]
            if self.manifest_sources:
                self.source_combo.current(0)
            self.set_busy(False, "就绪")
            self._switch_view(self._start_view)
            if self._auto_prepare_cdk:
                self.after(500, self.on_prepare_cdk_env)

        self.after(0, _apply)

    def on_search(self) -> None:
        self.current_view.set("home")
        self.list_page = 1
        self._reload_view()

    def _on_search_done(self, future) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                results: List[GameSearchResult] = future.result()
            except Exception as e:
                messagebox.showerror("搜索失败", str(e))
                return
            if not results:
                messagebox.showinfo("搜索", "未找到匹配游戏")
                return
            entries = [
                ManifestGameEntry(
                    appid=str(r.appid),
                    name=r.name,
                    installed=any(g.appid == str(r.appid) for g in self.service.get_installed_games()),
                )
                for r in results
            ]
            self.current_view.set("catalog")
            self.runner.submit(self.service.entries_to_cards(entries, 24)).add_done_callback(
                lambda f: self.after(0, lambda: self._show_search_cards(f, entries))
            )

        self.after(0, _apply)

    def _show_search_cards(self, future, entries) -> None:
        try:
            cards = future.result()
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            return
        self.cards = cards
        self.list_hint = "搜索结果"
        self._render_grid(cards)
        self.list_info_label.config(text=f"搜索结果 · {len(cards)} 款")

    def _resolve_app_id(self) -> Optional[str]:
        if self.selected_app_id:
            return self.selected_app_id
        q = self.search_var.get().strip()
        if q.isdigit():
            return q
        return self.service.backend.extract_app_id(q) if q else None

    def _current_options(self) -> ImportOptions:
        return ImportOptions(
            auto_update_manifest=self.auto_update_var.get(),
            add_all_dlc=self.add_dlc_var.get(),
            patch_workshop_key=False,
        )

    def on_import(self) -> None:
        if self._ui_dev:
            self._ui_dev_notice("入库")
            return
        if not self._require_vip("入库"):
            return
        app_id = self._resolve_app_id()
        if not app_id:
            messagebox.showwarning("提示", "请先点击游戏卡片")
            return
        card = next((c for c in self.cards if c.appid == app_id), None)
        if (
            card
            and not card.in_manifest
            and not card.installed
            and not self.auto_fallback_var.get()
        ):
            messagebox.showwarning("提示", "该游戏暂无清单，无法入库\n可在设置中开启「失败自动换源」后重试")
            return
        source = self._default_import_source()
        if not source:
            self._ensure_manifest_sources()
            source = self._default_import_source()
        if not source:
            return
        if not messagebox.askyesno("确认入库", f"AppID: {app_id}\n清单源: {source.name}"):
            return
        options = self._current_options()
        github_repo = source.repo if source.kind in ("builtin_github", "custom_github") else None
        self.set_busy(True, f"入库 {app_id}…")
        coro = (
            self.service.import_game_with_fallback(app_id, source, options, github_repo=github_repo)
            if self.auto_fallback_var.get()
            else self.service.import_game(app_id, source, options, github_repo=github_repo)
        )
        self.runner.submit(coro).add_done_callback(lambda f: self._on_import_done(f, app_id))

    def on_import_all(self) -> None:
        if self._ui_dev:
            self._ui_dev_notice("批量入库")
            return
        if not self._require_vip("批量入库"):
            return
        if self._import_all_running:
            messagebox.showinfo("提示", "批量入库正在进行中，请稍候")
            return
        if not self.cards:
            messagebox.showwarning("提示", "当前没有可入库的游戏")
            return

        to_import = [c.appid for c in self.cards if not c.installed and c.in_manifest]
        if not to_import:
            messagebox.showinfo("提示", "当前列表中的游戏均已入库")
            return

        source = self._default_import_source()
        if not source:
            self._ensure_manifest_sources()
            source = self._default_import_source()
        if not source:
            return

        skipped = len(self.cards) - len(to_import)
        skip_hint = f"\n已跳过 {skipped} 款已入库游戏" if skipped else ""
        if not messagebox.askyesno(
            "确认批量入库",
            f"将入库当前列表中的 {len(to_import)} 款游戏{skip_hint}\n"
            f"清单源: {source.name}\n\n"
            "过程可能较久，是否继续？",
        ):
            return

        options = self._current_options()
        github_repo = source.repo if source.kind in ("builtin_github", "custom_github") else None
        self._import_all_running = True
        self.import_all_btn.config(state="disabled")
        self.set_busy(True, f"批量入库 0/{len(to_import)}…")

        def _progress(current: int, total: int, app_id: str) -> None:
            self.after(0, lambda: self.set_busy(True, f"批量入库 {current}/{total} · {app_id}"))

        async def _batch():
            return await self.service.import_games_batch(
                to_import,
                source,
                options,
                auto_fallback=self.auto_fallback_var.get(),
                github_repo=github_repo,
                progress_callback=_progress,
            )

        self.runner.submit(_batch()).add_done_callback(self._on_import_all_done)

    def _on_import_all_done(self, future) -> None:
        def _apply() -> None:
            self._import_all_running = False
            self.import_all_btn.config(state="normal")
            try:
                result: BulkImportResult = future.result()
            except Exception as e:
                self.set_busy(False, "")
                messagebox.showerror("批量入库失败", str(e))
                return

            if not result.succeeded and not result.failed:
                self.set_busy(False, "")
                messagebox.showinfo("提示", "没有需要入库的游戏")
                return

            summary = f"成功 {result.success_count} 款"
            if result.fail_count:
                summary += f"，失败 {result.fail_count} 款"

            if result.succeeded and self.auto_finalize_var.get():
                self.set_busy(True, "部署注入…")
                self.runner.submit(self.service.finalize_batch_import(result.succeeded)).add_done_callback(
                    lambda f: self._on_batch_finalize_done(f, result, summary)
                )
            elif result.succeeded:
                self.set_busy(False, "")
                detail = self._format_bulk_result(result)
                messagebox.showinfo("批量入库完成", f"{summary}\n\n{detail}")
                self._reload_view()
            else:
                self.set_busy(False, "")
                detail = self._format_bulk_result(result)
                messagebox.showerror("批量入库失败", f"{summary}\n\n{detail}")

        self.after(0, _apply)

    def _format_bulk_result(self, result: BulkImportResult, limit: int = 8) -> str:
        lines: List[str] = []
        if result.succeeded:
            preview = ", ".join(result.succeeded[:limit])
            if len(result.succeeded) > limit:
                preview += f" 等 {len(result.succeeded)} 款"
            lines.append(f"成功: {preview}")
        if result.failed:
            for app_id, msg in result.failed[:limit]:
                short = msg.split("\n")[0][:60]
                lines.append(f"失败 {app_id}: {short}")
            if len(result.failed) > limit:
                lines.append(f"…另有 {len(result.failed) - limit} 款失败")
        return "\n".join(lines)

    def _on_batch_finalize_done(self, future, result: BulkImportResult, summary: str) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                ok, msg = future.result()
                detail = self._format_bulk_result(result)
                body = f"{summary}\n\n{msg}"
                if result.fail_count:
                    body += f"\n\n{detail}"
                if ok:
                    messagebox.showinfo("批量入库完成", body)
                else:
                    messagebox.showwarning("批量入库完成", body)
            except Exception as e:
                messagebox.showerror("失败", str(e))
            self._reload_view()

        self.after(0, _apply)

    def _on_import_done(self, future, app_id: str) -> None:
        def _apply() -> None:
            try:
                result: ImportResult = future.result()
            except Exception as e:
                self.set_busy(False, "")
                messagebox.showerror("入库失败", str(e))
                return
            if result.success and self.auto_finalize_var.get():
                self.set_busy(True, "部署注入…")
                self.runner.submit(self.service.finalize_one_click_import(app_id)).add_done_callback(
                    lambda f: self._on_finalize_done(f)
                )
            elif result.success:
                self.set_busy(False, "")
                messagebox.showinfo("入库成功", result.message)
                self._reload_view()
            else:
                self.set_busy(False, "")
                messagebox.showerror("入库失败", result.message)

        self.after(0, _apply)

    def _on_finalize_done(self, future) -> None:
        def _apply() -> None:
            self.set_busy(False, "")
            try:
                ok, msg = future.result()
                if ok:
                    messagebox.showinfo("完成", msg)
                else:
                    messagebox.showwarning("完成", msg)
            except Exception as e:
                messagebox.showerror("失败", str(e))
            self._reload_view()

        self.after(0, _apply)

    def _run_repair_injector(self) -> None:
        async def _r():
            ok, msg = await self.service.ensure_builtin_injection()
            if ok:
                self.service.restart_steam_client()
            return ok, msg

        self.runner.submit(_r()).add_done_callback(
            lambda f: self.after(0, lambda: messagebox.showinfo("修复", f.result()[1] if f.result()[0] else f.result()[1]))
        )

    def on_save_config(self) -> None:
        async def _save():
            config = await self.service.load_config()
            config["Custom_Steam_Path"] = self.steam_path_var.get().strip()
            url = self.box_server_url_var.get().strip().rstrip("/")
            config["Box_Server_URL"] = url
            await self.service.save_config(config)
            await self.service.initialize()

        def _done(future) -> None:
            try:
                future.result()
                self.runner.submit(self.service.load_config()).add_done_callback(
                    lambda f2: self.after(0, lambda: self._on_config_saved(f2))
                )
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        self.runner.submit(_save()).add_done_callback(lambda f: self.after(0, lambda: _done(f)))

    def _on_config_saved(self, future) -> None:
        try:
            config = future.result()
            self._apply_settings(config)
            self.manifest_sources = self.service.get_manifest_sources()
            if self.manifest_sources:
                self.source_combo["values"] = [s.name for s in self.manifest_sources]
                self.source_combo.current(0)
            url = str(config.get("Box_Server_URL", "")).strip()
            if url and hasattr(self, "box_server_status"):
                self.box_server_status.config(text=f"已保存，盒子将使用线上账号：{url}", fg=SUCCESS)
            elif hasattr(self, "box_server_status"):
                self.box_server_status.config(text="未配置线上地址，使用本机独立账号", fg=MUTED)
            messagebox.showinfo("保存", "配置已更新\n若修改了线上地址，建议退出登录后重新登录")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _apply_settings(self, config: dict) -> None:
        self._config = config
        self._setup_auth_from_config(config)
        self.steam_path_var.set(config.get("Custom_Steam_Path", ""))
        if hasattr(self, "box_server_url_var"):
            self.box_server_url_var.set(str(config.get("Box_Server_URL", "")).strip())

    def destroy(self) -> None:
        try:
            self.runner.submit(self.service.shutdown()).result(timeout=5)
        except Exception:
            pass
        self.runner.stop()
        super().destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam 游戏盒子")
    parser.add_argument("--cdk", action="store_true", help="启动后进入 CDK 激活页")
    parser.add_argument("--prepare", action="store_true", help="启动后自动部署激活环境")
    parser.add_argument(
        "--ui-dev",
        action="store_true",
        help="UI 开发模式（Mac 友好）：跳过 Steam/登录，使用示例数据预览界面",
    )
    args = parser.parse_args()
    start_view = "cdk" if args.cdk else "home"
    GameBoxApp(
        start_view=start_view,
        auto_prepare_cdk=args.prepare and not args.ui_dev,
        ui_dev=args.ui_dev,
    ).mainloop()


if __name__ == "__main__":
    main()
