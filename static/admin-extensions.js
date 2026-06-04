/** 扩展功能页：页内子导航 */
(function () {
  const EXT_TABS = [
    ["payment", "💳", "在线支付"],
    ["notify", "🔔", "消息通知"],
    ["security", "🛡", "安全 / 2FA"],
    ["api", "🔌", "开放 API"],
    ["tenant", "🏢", "多租户"],
    ["mall", "🛒", "CDK 商城"],
    ["stats", "📈", "数据统计"],
    ["sync-webhook", "🔗", "清单 Webhook"],
    ["agent-billing", "🧾", "代理信用 / 发票"],
    ["game-ops", "🎮", "游戏运维"],
  ];

  window.initExtensionsPage = function () {
    const nav = document.getElementById("extSideNav");
    if (!nav || nav.dataset.inited) return;
    nav.dataset.inited = "1";
    nav.innerHTML = EXT_TABS.map(([k, icon, label]) =>
      `<button type="button" class="side-nav-item" data-ext="${k}" onclick="switchExtTab('${k}')"><span class="icon">${icon}</span>${label}</button>`
    ).join("");
  };

  window.switchExtTab = function (key, skipRefresh) {
    if (typeof currentExtTab !== "undefined") currentExtTab = key;
    document.querySelectorAll("#extSideNav .side-nav-item").forEach(el => {
      el.classList.toggle("active", el.dataset.ext === key);
    });
    document.querySelectorAll("#extPanels .ui-tab-panel").forEach(p => {
      p.classList.toggle("active", p.id === "ext-" + key);
    });
    const meta = (typeof PAGE_META !== "undefined" && PAGE_META[key]) || { title: key, desc: "" };
    const titleEl = document.getElementById("pageTitle");
    const descEl = document.getElementById("pageDesc");
    if (titleEl) titleEl.textContent = meta.title;
    if (descEl) descEl.textContent = meta.desc;
    if (!skipRefresh) refreshExtTab(key);
  };

  window.refreshExtTab = function (key) {
    if (key === "stats" && typeof loadExtCharts === "function") loadExtCharts();
    if (key === "sync-webhook" && typeof pollExtSync === "function") pollExtSync();
  };

  async function extApi(path, opts) {
    return api(path, opts);
  }

  window.loadExtensionsData = async function () {
    const d = await extApi("/api/admin/extensions");
    if (!d.ok) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
    const chk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
    const p = d.payment || {};
    chk("epay_enabled", p.epay_enabled);
    set("epay_gateway", p.epay_gateway || "https://pay.maihao.la");
    set("epay_pid", p.epay_pid || "");
    set("epay_md5_key", p.epay_md5_key || "");
    set("epay_pay_type", p.epay_pay_type || "alipay");
    set("epay_checkout_mode", p.epay_checkout_mode || "cashier");
    set("epay_platform_public_key", p.epay_platform_public_key || "");
    chk("pay_usdt", p.usdt_enabled);
    set("usdt_address", p.usdt_address || "");
    set("usdt_rate", p.usdt_rate || 7.2);
    set("callback_secret", p.callback_secret || "");
    const base = (document.getElementById("topUrl")?.textContent || "").trim().replace(/\/$/, "");
    const nu = document.getElementById("epayNotifyUrl");
    const ru = document.getElementById("epayReturnUrl");
    const cu = document.getElementById("epayCashierUrl");
    if (nu) nu.textContent = (base || "") + "/api/payment/epay/notify";
    if (ru) ru.textContent = (base || "") + "/api/payment/epay/return";
    if (cu) cu.textContent = (base || "") + "/pay";
    const n = d.notifications || {};
    chk("tg_en", n.telegram_enabled);
    set("tg_token", n.telegram_bot_token || "");
    set("tg_chat", n.telegram_chat_id || "");
    chk("wecom_en", n.wecom_enabled);
    set("wecom_url", n.wecom_webhook || "");
    chk("email_en", n.email_enabled);
    set("smtp_host", n.smtp_host || "");
    set("smtp_port", n.smtp_port || 465);
    set("smtp_user", n.smtp_user || "");
    set("smtp_from", n.smtp_from || "");
    const s = d.security || {};
    chk("ip_en", s.ip_whitelist_enabled);
    const ipList = document.getElementById("ip_list");
    if (ipList) ipList.value = (s.ip_whitelist || []).join("\n");
    chk("confirm_pwd", s.require_confirm_password !== false);
    chk("tenant_en", (d.tenants || {}).enabled);
    const tl = document.getElementById("tenant_list");
    if (tl) tl.textContent = JSON.stringify((d.tenants || {}).sites || [], null, 2);
    chk("mall_en", (d.mall || {}).enabled);
    const pl = document.getElementById("pkg_list");
    if (pl) pl.textContent = JSON.stringify(d.packages || [], null, 2);
    const al = document.getElementById("api_list");
    if (al) al.textContent = JSON.stringify(d.api_keys || [], null, 2);
  };

  window.saveExtSection = async function (section) {
    let data = {};
    if (section === "payment") data = {
      epay_enabled: document.getElementById("epay_enabled")?.checked,
      epay_gateway: (document.getElementById("epay_gateway")?.value || "").trim() || "https://pay.maihao.la",
      epay_pid: (document.getElementById("epay_pid")?.value || "").trim(),
      epay_md5_key: (document.getElementById("epay_md5_key")?.value || "").trim(),
      epay_pay_type: document.getElementById("epay_pay_type")?.value || "alipay",
      epay_checkout_mode: document.getElementById("epay_checkout_mode")?.value || "cashier",
      epay_platform_public_key: (document.getElementById("epay_platform_public_key")?.value || "").trim(),
      usdt_enabled: document.getElementById("pay_usdt")?.checked,
      usdt_address: document.getElementById("usdt_address")?.value,
      usdt_rate: parseFloat(document.getElementById("usdt_rate")?.value) || 7.2,
      callback_secret: document.getElementById("callback_secret")?.value,
    };
    if (section === "notifications") data = {
      telegram_enabled: document.getElementById("tg_en")?.checked,
      telegram_bot_token: document.getElementById("tg_token")?.value,
      telegram_chat_id: document.getElementById("tg_chat")?.value,
      wecom_enabled: document.getElementById("wecom_en")?.checked,
      wecom_webhook: document.getElementById("wecom_url")?.value,
      email_enabled: document.getElementById("email_en")?.checked,
      smtp_host: document.getElementById("smtp_host")?.value,
      smtp_port: parseInt(document.getElementById("smtp_port")?.value, 10) || 465,
      smtp_user: document.getElementById("smtp_user")?.value,
      smtp_password: document.getElementById("smtp_pass")?.value,
      smtp_from: document.getElementById("smtp_from")?.value,
    };
    if (section === "security") data = {
      ip_whitelist_enabled: document.getElementById("ip_en")?.checked,
      ip_whitelist: (document.getElementById("ip_list")?.value || "").split("\n").map(s => s.trim()).filter(Boolean),
      require_confirm_password: document.getElementById("confirm_pwd")?.checked,
    };
    if (section === "tenants") data = { enabled: document.getElementById("tenant_en")?.checked };
    if (section === "mall") data = { enabled: document.getElementById("mall_en")?.checked };
    const r = await extApi("/api/admin/extensions/save", { method: "POST", body: { section, data } });
    toast(r.message || (r.ok ? "已保存" : "失败"), r.ok ? "ok" : "err");
  };

  window.testExtNotify = async () => {
    const r = await extApi("/api/admin/extensions/test-notify", { method: "POST", body: {} });
    toast(r.ok ? "测试通知已发送" : (r.message || "失败"), r.ok ? "ok" : "err");
  };
  window.testEpayConnection = async () => {
    const el = document.getElementById("epay_test_result");
    if (el) { el.classList.remove("hidden"); el.textContent = "测试中…"; }
    const r = await extApi("/api/admin/payment/test-epay", { method: "POST", body: {} });
    if (el) el.textContent = r.ok ? JSON.stringify(r.merchant || r, null, 2) : (r.message || "连接失败");
    toast(r.ok ? "GoPay 连接成功" : (r.message || "连接失败"), r.ok ? "ok" : "err");
  };
  window.setup2fa = async () => {
    const r = await extApi("/api/admin/security/2fa/setup", { method: "POST", body: {} });
    const el = document.getElementById("twofa_info");
    if (el) el.textContent = JSON.stringify(r, null, 2);
  };
  window.enable2fa = async () => {
    const r = await extApi("/api/admin/security/2fa/enable", { method: "POST", body: { code: document.getElementById("totp_code")?.value } });
    toast(r.message || (r.ok ? "2FA 已启用" : "失败"), r.ok ? "ok" : "err");
  };
  window.disable2fa = async () => {
    await extApi("/api/admin/security/2fa/disable", { method: "POST", body: {} });
    toast("2FA 已关闭", "ok");
  };
  window.createApiKey = async () => {
    const r = await extApi("/api/admin/api-keys/create", { method: "POST", body: { name: document.getElementById("api_name")?.value || "default" } });
    const el = document.getElementById("api_key_result");
    if (el) el.textContent = r.key ? "Key（仅显示一次）: " + r.key : JSON.stringify(r, null, 2);
    loadExtensionsData();
  };
  window.addTenantSite = async () => {
    await extApi("/api/admin/tenants/add", { method: "POST", body: { name: document.getElementById("t_name")?.value, domain: document.getElementById("t_domain")?.value } });
    loadExtensionsData();
    toast("站点已添加", "ok");
  };
  window.saveExtPackage = async () => {
    await extApi("/api/admin/cdk/packages", { method: "POST", body: {
      name: document.getElementById("pkg_name")?.value,
      appid: document.getElementById("pkg_appid")?.value,
      price_cny: parseFloat(document.getElementById("pkg_price")?.value) || 0,
      expire_days: parseInt(document.getElementById("pkg_days")?.value, 10) || 0,
      enabled: true,
    }});
    loadExtensionsData();
    toast("套餐已保存", "ok");
  };
  window.batchImportCdk = async () => {
    const r = await extApi("/api/admin/cdk/batch-import", { method: "POST", body: {
      lines: document.getElementById("batch_lines")?.value,
      appid: document.getElementById("batch_appid")?.value,
    }});
    toast(`导入 ${r.added || 0} 条，跳过 ${r.skipped || 0} 条`, r.ok ? "ok" : "err");
  };
  window.loadExtCharts = async () => {
    const d = await extApi("/api/admin/stats/charts");
    const el = document.getElementById("charts_data");
    if (el) el.textContent = JSON.stringify(d, null, 2);
  };
  window.exportExtCsv = () => {
    const base = document.getElementById("topUrl")?.textContent?.trim() || "";
    window.open(base + "/api/admin/stats/export.xlsx?token=" + encodeURIComponent(token), "_blank");
  };
  window.pollExtSync = async () => {
    const d = await extApi("/api/admin/sync/progress");
    const el = document.getElementById("sync_prog");
    if (el) el.textContent = JSON.stringify(d, null, 2);
  };
  window.setAgentCredit = async () => {
    const r = await extApi("/api/admin/agent/credit", { method: "POST", body: {
      user_id: document.getElementById("credit_uid")?.value,
      credit_limit: parseFloat(document.getElementById("credit_limit")?.value) || 0,
    }});
    toast(r.message || (r.ok ? "已设置" : "失败"), r.ok ? "ok" : "err");
  };
  window.createExtInvoice = async () => {
    const r = await extApi("/api/admin/invoices/create", { method: "POST", body: {
      user_id: document.getElementById("inv_uid")?.value,
      month: document.getElementById("inv_month")?.value,
    }});
    const el = document.getElementById("inv_list");
    if (el) el.textContent = JSON.stringify(r, null, 2);
  };
  window.gameExtOp = async (op) => {
    const url = op === "uninstall" ? "/api/admin/games/uninstall" : "/api/admin/games/workshop";
    const r = await extApi(url, { method: "POST", body: { appid: document.getElementById("g_appid")?.value } });
    const el = document.getElementById("game_result");
    if (el) el.textContent = JSON.stringify(r, null, 2);
  };
  window.importExtDlc = async () => {
    const ids = (document.getElementById("g_dlcs")?.value || "").split(",").map(s => s.trim()).filter(Boolean);
    const r = await extApi("/api/admin/games/import-dlc", { method: "POST", body: { appid: document.getElementById("g_appid")?.value, dlc_ids: ids } });
    const el = document.getElementById("game_result");
    if (el) el.textContent = JSON.stringify(r, null, 2);
  };
})();
