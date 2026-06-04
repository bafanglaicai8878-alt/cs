/** 管理后台：激活教程编辑器 */
(function () {
  let helpData = null;
  let helpCurrentTab = "hero";

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function val(id) {
    const el = document.getElementById(id);
    return el ? el.value : "";
  }

  function chk(id) {
    const el = document.getElementById(id);
    return el ? !!el.checked : false;
  }

  function setVal(id, v) {
    const el = document.getElementById(id);
    if (el) el.value = v == null ? "" : v;
  }

  function setChk(id, v) {
    const el = document.getElementById(id);
    if (el) el.checked = !!v;
  }

  function linesToList(text) {
    return String(text || "")
      .split("\n")
      .map(s => s.trim())
      .filter(Boolean);
  }

  function listToLines(arr) {
    return (arr || []).join("\n");
  }

  window.switchHelpTab = function (key) {
    helpCurrentTab = key;
    document.querySelectorAll("#helpSideNav .side-nav-item").forEach(el => {
      el.classList.toggle("active", el.dataset.helpTab === key);
    });
    document.querySelectorAll("#helpPanels .ui-tab-panel").forEach(el => {
      el.classList.toggle("active", el.id === "help-tab-" + key);
    });
  };

  function renderStepsEditor(steps) {
    const box = document.getElementById("helpStepsEditor");
    if (!box) return;
    box.innerHTML = "";
    if (!steps || !steps.length) {
      box.innerHTML = '<div class="help-empty-hint">暂无步骤，点击右上角「添加步骤」开始配置</div>';
      return;
    }
    (steps || []).forEach((step, si) => {
      const wrap = document.createElement("div");
      wrap.className = "help-step-block";
      wrap.innerHTML = `
        <div class="help-step-head">
          <div class="step-title-label"><span class="step-badge">${si + 1}</span>${esc(step.title || "未命名步骤")}</div>
          <div class="help-step-actions">
            <button type="button" class="btn ghost small" data-act="up" title="上移">↑</button>
            <button type="button" class="btn ghost small" data-act="down" title="下移">↓</button>
            <button type="button" class="btn ghost small" data-act="del" title="删除">删除</button>
          </div>
        </div>
        <div class="help-step-body-inner">
          <div class="form-grid-2">
            <div class="field"><label>标题</label><input class="step-title" value="${esc(step.title || "")}" /></div>
            <div class="field"><label style="display:flex;gap:8px;align-items:center;margin-top:22px"><input type="checkbox" class="step-show-cmd" ${step.show_primary_cmd ? "checked" : ""} /> 本步显示活动口令</label></div>
          </div>
          <div class="field"><label>说明</label><textarea class="step-body" rows="3">${esc(step.body || "")}</textarea></div>
          <div class="help-images-block">
            <label>配图</label>
            <div class="help-images"></div>
            <button type="button" class="btn secondary small add-img" style="margin-top:8px">+ 添加配图</button>
          </div>
        </div>
      `;
      const titleInput = wrap.querySelector(".step-title");
      const badgeLabel = wrap.querySelector(".step-title-label");
      titleInput.addEventListener("input", () => {
        badgeLabel.innerHTML = `<span class="step-badge">${si + 1}</span>${esc(titleInput.value.trim() || "未命名步骤")}`;
      });

      const imgBox = wrap.querySelector(".help-images");
      (step.images || []).forEach(img => {
        imgBox.appendChild(buildImageRow(img));
      });
      wrap.querySelector(".add-img").onclick = () => {
        imgBox.appendChild(buildImageRow({ url: "", caption: "" }));
      };
      wrap.querySelector('[data-act="del"]').onclick = () => {
        helpData.steps.splice(si, 1);
        renderStepsEditor(helpData.steps);
      };
      wrap.querySelector('[data-act="up"]').onclick = () => {
        if (si <= 0) return;
        [helpData.steps[si - 1], helpData.steps[si]] = [helpData.steps[si], helpData.steps[si - 1]];
        renderStepsEditor(helpData.steps);
      };
      wrap.querySelector('[data-act="down"]').onclick = () => {
        if (si >= helpData.steps.length - 1) return;
        [helpData.steps[si + 1], helpData.steps[si]] = [helpData.steps[si], helpData.steps[si + 1]];
        renderStepsEditor(helpData.steps);
      };
      box.appendChild(wrap);
    });
  }

  function buildImageRow(img) {
    const row = document.createElement("div");
    row.className = "help-list-row cols-2 help-img-row";
    row.innerHTML = `
      <div class="field"><label>图片 URL</label><input class="img-url" value="${esc(img.url || "")}" placeholder="/static/help/step1.png" /></div>
      <div class="field"><label>说明文字</label><input class="img-cap" value="${esc(img.caption || "")}" /></div>
      <button type="button" class="btn ghost small rm-img">删</button>
    `;
    row.querySelector(".rm-img").onclick = () => row.remove();
    return row;
  }

  function renderFallbackEditor(items) {
    const box = document.getElementById("helpFallbackEditor");
    if (!box) return;
    box.innerHTML = "";
    if (!items || !items.length) {
      box.innerHTML = '<div class="help-empty-hint" style="margin-bottom:8px">暂无备用口令（可选）</div>';
      return;
    }
    (items || []).forEach(fb => {
      const row = document.createElement("div");
      row.className = "help-list-row cols-fb";
      row.innerHTML = `
        <div class="field"><label>标签</label><input class="fb-label" value="${esc(fb.label || "")}" /></div>
        <div class="field"><label>口令</label><input class="fb-cmd" value="${esc(fb.cmd || "")}" style="font-family:ui-monospace,monospace" /></div>
        <button type="button" class="btn ghost small rm-fb">删</button>
      `;
      row.querySelector(".rm-fb").onclick = () => row.remove();
      box.appendChild(row);
    });
  }

  function renderLinksEditor(links) {
    const box = document.getElementById("helpLinksEditor");
    if (!box) return;
    box.innerHTML = "";
    if (!links || !links.length) {
      box.innerHTML = '<div class="help-empty-hint" style="margin-bottom:8px">暂无底部链接（可选）</div>';
      return;
    }
    (links || []).forEach(lk => {
      const row = document.createElement("div");
      row.className = "help-list-row cols-2";
      row.innerHTML = `
        <div class="field"><label>文字</label><input class="lk-label" value="${esc(lk.label || "")}" /></div>
        <div class="field"><label>链接</label><input class="lk-url" value="${esc(lk.url || "")}" /></div>
        <button type="button" class="btn ghost small rm-lk">删</button>
      `;
      row.querySelector(".rm-lk").onclick = () => row.remove();
      box.appendChild(row);
    });
  }

  function renderCarouselEditor(images) {
    const box = document.getElementById("helpCarouselEditor");
    if (!box) return;
    box.innerHTML = "";
    if (!images || !images.length) {
      box.innerHTML = '<div class="help-empty-hint" style="margin-bottom:8px">暂无轮播图（可选）</div>';
      return;
    }
    (images || []).forEach(img => {
      const row = document.createElement("div");
      row.className = "help-list-row cols-2";
      row.innerHTML = `
        <div class="field"><label>图片 URL</label><input class="cr-url" value="${esc(img.url || "")}" /></div>
        <div class="field"><label>说明</label><input class="cr-cap" value="${esc(img.caption || "")}" /></div>
        <button type="button" class="btn ghost small rm-cr">删</button>
      `;
      row.querySelector(".rm-cr").onclick = () => row.remove();
      box.appendChild(row);
    });
  }

  function collectStepsFromDom() {
    const blocks = document.querySelectorAll("#helpStepsEditor .help-step-block");
    const steps = [];
    blocks.forEach(block => {
      const images = [];
      block.querySelectorAll(".help-img-row").forEach(row => {
        images.push({
          url: row.querySelector(".img-url").value.trim(),
          caption: row.querySelector(".img-cap").value.trim(),
        });
      });
      const showCmdEl = block.querySelector(".step-show-cmd");
      steps.push({
        title: block.querySelector(".step-title").value.trim(),
        body: block.querySelector(".step-body").value.trim(),
        images,
        show_primary_cmd: showCmdEl ? showCmdEl.checked : false,
        show_fallback_cmds: showCmdEl ? showCmdEl.checked : false,
      });
    });
    return steps;
  }

  function collectFallbackFromDom() {
    const out = [];
    document.querySelectorAll("#helpFallbackEditor .help-list-row").forEach(row => {
      out.push({
        label: row.querySelector(".fb-label").value.trim(),
        cmd: row.querySelector(".fb-cmd").value.trim(),
      });
    });
    return out;
  }

  function collectLinksFromDom() {
    const out = [];
    document.querySelectorAll("#helpLinksEditor .help-list-row").forEach(row => {
      out.push({
        label: row.querySelector(".lk-label").value.trim(),
        url: row.querySelector(".lk-url").value.trim(),
      });
    });
    return out;
  }

  function collectCarouselFromDom() {
    const out = [];
    document.querySelectorAll("#helpCarouselEditor .help-list-row").forEach(row => {
      out.push({
        url: row.querySelector(".cr-url").value.trim(),
        caption: row.querySelector(".cr-cap").value.trim(),
      });
    });
    return out;
  }

  function fillForm(data) {
    helpData = JSON.parse(JSON.stringify(data));
    setChk("helpEnabled", data.enabled);
    setVal("helpPageTitle", data.page_title);
    setVal("helpNavBrand", data.nav_brand);
    setVal("helpHeroBadge", data.hero_badge);
    setVal("helpHeroTitle", data.hero_title);
    setVal("helpHeroSubtitle", data.hero_subtitle);
    setChk("helpGateEnabled", data.password_gate_enabled);
    setVal("helpGateTitle", data.password_gate_title);
    setVal("helpGateDesc", data.password_gate_desc);
    setVal("helpGateSecret", data.password_gate_secret || "");
    setVal("helpGateRedirect", data.password_gate_redirect || "");
    setChk("helpInstallerEnabled", data.installer_enabled);
    setVal("helpInstallerTitle", data.installer_title);
    setVal("helpInstallerDesc", data.installer_desc);
    setVal("helpInstallerBtn", data.installer_btn_label);
    setVal("helpInstallerUrl", data.installer_url);
    setVal("helpInstallerBtn2", data.installer_btn2_label);
    setVal("helpInstallerBtn2Url", data.installer_btn2_url);
    setVal("helpManualDivider", data.manual_divider);
    setVal("helpStepsTitle", data.steps_section_title);
    setVal("helpPrimaryLabel", data.primary_cmd_label);
    setVal("helpPrimaryCmd", data.primary_cmd);
    setVal("helpNoticesTitle", data.notices_title);
    setVal("helpNotices", listToLines(data.notices));
    setChk("helpTipsEnabled", data.tips_enabled);
    setVal("helpTipsSection", data.tips_section_title);
    setVal("helpTipsCard", data.tips_card_title);
    setVal("helpTipsHeading", data.tips_heading);
    setVal("helpTipsBody", data.tips_body);
    setVal("helpExtraTitle", data.extra_links_title);
    setVal("helpFooter", data.footer_text);
    setChk("helpCarouselEnabled", data.carousel_enabled);
    renderStepsEditor(data.steps);
    renderFallbackEditor(data.fallback_cmds);
    renderLinksEditor(data.extra_links);
    renderCarouselEditor(data.carousel_images);
  }

  function buildPayload() {
    return {
      enabled: chk("helpEnabled"),
      page_title: val("helpPageTitle").trim(),
      nav_brand: val("helpNavBrand").trim(),
      hero_badge: val("helpHeroBadge").trim(),
      hero_title: val("helpHeroTitle").trim(),
      hero_subtitle: val("helpHeroSubtitle").trim(),
      password_gate_enabled: chk("helpGateEnabled"),
      password_gate_title: val("helpGateTitle").trim(),
      password_gate_desc: val("helpGateDesc").trim(),
      password_gate_secret: val("helpGateSecret").trim(),
      password_gate_redirect: val("helpGateRedirect").trim(),
      installer_enabled: chk("helpInstallerEnabled"),
      installer_title: val("helpInstallerTitle").trim(),
      installer_desc: val("helpInstallerDesc").trim(),
      installer_btn_label: val("helpInstallerBtn").trim(),
      installer_url: val("helpInstallerUrl").trim(),
      installer_btn2_label: val("helpInstallerBtn2").trim(),
      installer_btn2_url: val("helpInstallerBtn2Url").trim(),
      manual_divider: val("helpManualDivider").trim(),
      steps_section_title: val("helpStepsTitle").trim(),
      primary_cmd_label: val("helpPrimaryLabel").trim(),
      primary_cmd: val("helpPrimaryCmd").trim(),
      fallback_cmds: collectFallbackFromDom(),
      steps: collectStepsFromDom(),
      notices_title: val("helpNoticesTitle").trim(),
      notices: linesToList(val("helpNotices")),
      tips_enabled: chk("helpTipsEnabled"),
      tips_section_title: val("helpTipsSection").trim(),
      tips_card_title: val("helpTipsCard").trim(),
      tips_heading: val("helpTipsHeading").trim(),
      tips_body: val("helpTipsBody").trim(),
      extra_links_title: val("helpExtraTitle").trim(),
      extra_links: collectLinksFromDom(),
      footer_text: val("helpFooter").trim(),
      carousel_enabled: chk("helpCarouselEnabled"),
      carousel_images: collectCarouselFromDom(),
    };
  }

  async function loadHelpTutorialPage() {
    if (!window.currentUser || window.currentUser.role !== "superadmin") return;
    switchHelpTab(helpCurrentTab || "hero");
    const d = await api("/api/admin/help-tutorial");
    if (!d.ok) {
      toast(d.message || "加载失败", "err");
      return;
    }
    fillForm(d.tutorial);
  }

  async function saveHelpTutorial() {
    const payload = buildPayload();
    const d = await api("/api/admin/help-tutorial/save", {
      method: "POST",
      body: payload,
    });
    if (!d.ok) {
      toast(d.message || "保存失败", "err");
      return;
    }
    toast("教程已保存", "ok");
    if (d.tutorial) fillForm(d.tutorial);
  }

  function addHelpStep() {
    if (!helpData) helpData = { steps: [] };
    if (!helpData.steps) helpData.steps = [];
    helpData.steps.push({
      title: "新步骤",
      body: "",
      images: [],
    });
    switchHelpTab("steps");
    renderStepsEditor(helpData.steps);
  }

  function appendFallbackRow(label, cmd) {
    const box = document.getElementById("helpFallbackEditor");
    if (!box) return;
    const empty = box.querySelector(".help-empty-hint");
    if (empty) empty.remove();
    const row = document.createElement("div");
    row.className = "help-list-row cols-fb";
    row.innerHTML = `
      <div class="field"><label>标签</label><input class="fb-label" value="${esc(label || "备用口令")}" /></div>
      <div class="field"><label>口令</label><input class="fb-cmd" value="${esc(cmd || "")}" style="font-family:ui-monospace,monospace" /></div>
      <button type="button" class="btn ghost small rm-fb">删</button>
    `;
    row.querySelector(".rm-fb").onclick = () => row.remove();
    box.appendChild(row);
  }

  function appendLinkRow(label, url) {
    const box = document.getElementById("helpLinksEditor");
    if (!box) return;
    const empty = box.querySelector(".help-empty-hint");
    if (empty) empty.remove();
    const row = document.createElement("div");
    row.className = "help-list-row cols-2";
    row.innerHTML = `
      <div class="field"><label>文字</label><input class="lk-label" value="${esc(label || "")}" /></div>
      <div class="field"><label>链接</label><input class="lk-url" value="${esc(url || "#steps")}" /></div>
      <button type="button" class="btn ghost small rm-lk">删</button>
    `;
    row.querySelector(".rm-lk").onclick = () => row.remove();
    box.appendChild(row);
  }

  function appendCarouselRow() {
    const box = document.getElementById("helpCarouselEditor");
    if (!box) return;
    const empty = box.querySelector(".help-empty-hint");
    if (empty) empty.remove();
    const row = document.createElement("div");
    row.className = "help-list-row cols-2";
    row.innerHTML = `
      <div class="field"><label>图片 URL</label><input class="cr-url" value="" /></div>
      <div class="field"><label>说明</label><input class="cr-cap" value="" /></div>
      <button type="button" class="btn ghost small rm-cr">删</button>
    `;
    row.querySelector(".rm-cr").onclick = () => row.remove();
    box.appendChild(row);
  }

  window.loadHelpTutorialPage = loadHelpTutorialPage;
  window.saveHelpTutorial = saveHelpTutorial;
  window.addHelpStep = addHelpStep;
  window.addHelpFallback = function () {
    switchHelpTab("cmd");
    appendFallbackRow("备用口令", "");
  };
  window.addHelpLink = function () {
    switchHelpTab("footer");
    appendLinkRow("", "#steps");
  };
  window.addHelpCarousel = function () {
    switchHelpTab("footer");
    appendCarouselRow();
  };
})();
