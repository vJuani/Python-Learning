(function () {
  var root = document.querySelector("[data-mkt-chat]");
  if (!root) return;

  var SIDEBAR_KEY = "mkt-chat-sidebar-width";
  var SIDEBAR_MIN = 14;
  var SIDEBAR_MAX = 24;
  var MOBILE_MQ = "(max-width: 900px)";

  var prompt = root.querySelector("[data-mkt-chat-prompt]");
  var propertyId = root.querySelector("[data-mkt-property-id]");
  var chip = root.querySelector("[data-mkt-property-chip]");
  var chipLabel = root.querySelector("[data-mkt-property-label]");
  var picker = root.querySelector("[data-mkt-picker]");
  var search = root.querySelector("[data-mkt-property-search]");
  var thread = root.querySelector("[data-mkt-thread]");
  var composer = root.querySelector("[data-mkt-composer]");
  var generating = root.querySelector("[data-mkt-generating]");
  var historyBtn = root.querySelector("[data-mkt-history]");
  var sidebar = root.querySelector("[data-mkt-sidebar]");
  var resizeHandle = root.querySelector("[data-mkt-resize]");
  var chatSearch = root.querySelector("[data-mkt-chat-search]");
  var folderCreateBtn = root.querySelector("[data-mkt-folder-create]");
  var folderForm = root.querySelector("[data-mkt-folder-form]");
  var folderInput = root.querySelector("[data-mkt-folder-input]");
  var openCtx = null;

  var intentMode = root.querySelector("[data-mkt-intent-mode]");
  root.querySelectorAll("[data-mkt-mode]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (intentMode) intentMode.value = button.getAttribute("data-mkt-mode") || "auto";
      root.querySelectorAll("[data-mkt-mode]").forEach(function (item) {
        item.classList.toggle("is-active", item === button);
      });
    });
  });

  function isMobile() {
    return window.matchMedia(MOBILE_MQ).matches;
  }

  function setProperty(id, label) {
    if (propertyId) propertyId.value = id || "";
    if (chipLabel) chipLabel.textContent = label || "";
    if (chip) chip.hidden = !id;
  }

  function resizePrompt() {
    if (!prompt) return;
    prompt.style.height = "auto";
    prompt.style.height = Math.min(prompt.scrollHeight, 160) + "px";
  }

  function closeAllCtx() {
    root.querySelectorAll("[data-mkt-ctx]").forEach(function (menu) {
      menu.hidden = true;
    });
    root.querySelectorAll("[data-mkt-conv-menu]").forEach(function (button) {
      button.setAttribute("aria-expanded", "false");
    });
    openCtx = null;
  }

  function openCtxMenu(item, menuBtn) {
    if (!item) return;
    var menu = item.querySelector("[data-mkt-ctx]");
    if (!menu) return;
    var alreadyOpen = openCtx === menu && !menu.hidden;
    closeAllCtx();
    if (alreadyOpen) return;
    menu.hidden = false;
    openCtx = menu;
    if (menuBtn) menuBtn.setAttribute("aria-expanded", "true");
  }

  function postPromptForm(action, fieldName, value) {
    if (!action || value == null || String(value).trim() === "") return;
    var form = document.createElement("form");
    form.method = "post";
    form.action = action;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = fieldName;
    input.value = String(value).trim();
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
  }

  function applySidebarWidth(rem) {
    var clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, rem));
    root.style.setProperty("--mkt-sidebar-width", clamped + "rem");
    return clamped;
  }

  function restoreSidebarWidth() {
    if (isMobile()) return;
    try {
      var stored = localStorage.getItem(SIDEBAR_KEY);
      if (!stored) return;
      var rem = parseFloat(stored);
      if (!isFinite(rem)) return;
      applySidebarWidth(rem);
    } catch (err) {}
  }

  root.querySelectorAll("[data-mkt-starter]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!prompt) return;
      prompt.value = button.getAttribute("data-prompt") || "";
      resizePrompt();
      prompt.focus();
    });
  });

  if (prompt) {
    prompt.addEventListener("input", resizePrompt);
  }

  var openPickers = root.querySelectorAll("[data-mkt-open-picker]");
  if (picker) {
    openPickers.forEach(function (button) {
      button.addEventListener("click", function () {
        if (typeof picker.showModal === "function") picker.showModal();
      });
    });
  }

  root.querySelectorAll("[data-mkt-pick-property]").forEach(function (button) {
    button.addEventListener("click", function () {
      setProperty(button.getAttribute("data-id"), button.getAttribute("data-label"));
      if (picker && picker.open) picker.close();
    });
  });

  var clearProperty = root.querySelector("[data-mkt-clear-property]");
  if (clearProperty) {
    clearProperty.addEventListener("click", function () {
      setProperty("", "");
    });
  }

  if (search) {
    search.addEventListener("input", function () {
      var query = (search.value || "").toLowerCase();
      root.querySelectorAll("[data-mkt-pick-property]").forEach(function (card) {
        var haystack = (card.getAttribute("data-search") || "").toLowerCase();
        card.hidden = Boolean(query) && haystack.indexOf(query) === -1;
      });
    });
  }

  if (historyBtn) {
    historyBtn.addEventListener("click", function () {
      var open = document.body.classList.toggle("is-mkt-history-open");
      historyBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) closeAllCtx();
    });
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (openCtx) {
      var inOpen = openCtx.contains(target);
      var menuBtn = openCtx.parentElement && openCtx.parentElement.querySelector("[data-mkt-conv-menu]");
      var onMenuBtn = menuBtn && menuBtn.contains(target);
      if (!inOpen && !onMenuBtn) closeAllCtx();
    }

    if (!document.body.classList.contains("is-mkt-history-open")) return;
    if (sidebar && sidebar.contains(target)) return;
    if (historyBtn && historyBtn.contains(target)) return;
    document.body.classList.remove("is-mkt-history-open");
    if (historyBtn) historyBtn.setAttribute("aria-expanded", "false");
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (openCtx) {
        closeAllCtx();
        return;
      }
      if (document.body.classList.contains("is-mkt-history-open")) {
        document.body.classList.remove("is-mkt-history-open");
        if (historyBtn) historyBtn.setAttribute("aria-expanded", "false");
      }
    }
  });

  root.addEventListener("click", function (event) {
    var menuBtn = event.target.closest("[data-mkt-conv-menu]");
    if (menuBtn && root.contains(menuBtn)) {
      event.preventDefault();
      event.stopPropagation();
      openCtxMenu(menuBtn.closest("[data-mkt-conv-item]"), menuBtn);
      return;
    }

    var renameBtn = event.target.closest("[data-mkt-rename]");
    if (renameBtn && root.contains(renameBtn)) {
      event.preventDefault();
      var currentTitle = renameBtn.getAttribute("data-title") || "";
      var nextTitle = window.prompt(renameBtn.textContent.trim() || "Rename", currentTitle);
      closeAllCtx();
      if (nextTitle == null) return;
      postPromptForm(renameBtn.getAttribute("data-action"), "title", nextTitle);
      return;
    }

    var folderRenameBtn = event.target.closest("[data-mkt-folder-rename]");
    if (folderRenameBtn && root.contains(folderRenameBtn)) {
      event.preventDefault();
      var currentName = folderRenameBtn.getAttribute("data-name") || "";
      var nextName = window.prompt(folderRenameBtn.textContent.trim() || "Rename", currentName);
      if (nextName == null) return;
      postPromptForm(folderRenameBtn.getAttribute("data-action"), "name", nextName);
    }
  });

  root.addEventListener("contextmenu", function (event) {
    var item = event.target.closest("[data-mkt-conv-item]");
    if (!item || !root.contains(item)) return;
    if (event.target.closest("[data-mkt-ctx]")) return;
    event.preventDefault();
    openCtxMenu(item, item.querySelector("[data-mkt-conv-menu]"));
  });

  root.querySelectorAll("form[data-mkt-delete]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var message = form.getAttribute("data-confirm") || "";
      if (message && !window.confirm(message)) {
        event.preventDefault();
        return;
      }
      closeAllCtx();
    });
  });

  if (folderCreateBtn && folderForm) {
    folderCreateBtn.addEventListener("click", function () {
      var willShow = folderForm.hidden;
      folderForm.hidden = !willShow;
      if (willShow && folderInput) {
        folderInput.focus();
        folderInput.select();
      }
    });
  }

  if (chatSearch) {
    chatSearch.addEventListener("input", function () {
      var query = (chatSearch.value || "").trim().toLowerCase();
      root.querySelectorAll("[data-mkt-conv-item]").forEach(function (item) {
        var title = (item.getAttribute("data-title") || "").toLowerCase();
        item.hidden = Boolean(query) && title.indexOf(query) === -1;
      });
    });
  }

  restoreSidebarWidth();

  if (resizeHandle) {
    var dragging = false;
    var startX = 0;
    var startWidth = 17;

    function onPointerMove(event) {
      if (!dragging || isMobile()) return;
      var dx = event.clientX - startX;
      var rootFont = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      var nextRem = startWidth + dx / rootFont;
      applySidebarWidth(nextRem);
    }

    function stopDrag() {
      if (!dragging) return;
      dragging = false;
      resizeHandle.classList.remove("is-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      try {
        var current = root.style.getPropertyValue("--mkt-sidebar-width") || "";
        var rem = parseFloat(current);
        if (isFinite(rem)) localStorage.setItem(SIDEBAR_KEY, String(applySidebarWidth(rem)));
      } catch (err) {}
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stopDrag);
      window.removeEventListener("pointercancel", stopDrag);
    }

    resizeHandle.addEventListener("pointerdown", function (event) {
      if (isMobile() || event.button !== 0) return;
      event.preventDefault();
      dragging = true;
      startX = event.clientX;
      var current = root.style.getPropertyValue("--mkt-sidebar-width") || getComputedStyle(root).getPropertyValue("--mkt-sidebar-width") || "17rem";
      startWidth = parseFloat(current) || 17;
      resizeHandle.classList.add("is-dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", stopDrag);
      window.addEventListener("pointercancel", stopDrag);
    });

    window.addEventListener("resize", function () {
      if (isMobile()) {
        stopDrag();
      }
    });
  }

  if (composer) {
    composer.addEventListener("submit", function () {
      if (generating) generating.hidden = false;
    });
  }
  root.querySelectorAll("form[method='post']").forEach(function (form) {
    if (form === composer) return;
    if (form.hasAttribute("data-mkt-delete")) return;
    form.addEventListener("submit", function () {
      if (generating) generating.hidden = false;
    });
  });

  if (thread) {
    thread.scrollTop = thread.scrollHeight;
  }

  root.querySelectorAll("[data-mkt-edit]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!prompt) return;
      prompt.focus();
      if (!prompt.value) prompt.placeholder = prompt.getAttribute("placeholder") || "";
    });
  });

  var zoomDialog = root.querySelector("[data-mkt-zoom-dialog]");
  var zoomImg = zoomDialog && zoomDialog.querySelector("[data-mkt-zoom-img]");
  var zoomTitle = zoomDialog && zoomDialog.querySelector("[data-mkt-zoom-title]");
  if (zoomDialog && zoomImg && typeof zoomDialog.showModal === "function") {
    root.querySelectorAll("[data-mkt-zoom]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var title = link.getAttribute("data-title") || "";
        zoomImg.src = link.getAttribute("href") || "";
        zoomImg.alt = title;
        if (zoomTitle) zoomTitle.textContent = title;
        zoomDialog.showModal();
      });
    });
    zoomDialog.addEventListener("click", function (event) {
      if (event.target === zoomDialog) zoomDialog.close();
    });
  }

  root.querySelectorAll("[data-mkt-download]").forEach(function (button) {
    button.addEventListener("click", function () {
      var card = button.closest("[data-mkt-result]");
      var source = card && card.querySelector("[id^='mkt-copy-']");
      var text = source ? source.textContent || "" : "";
      var blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = button.getAttribute("data-download-name") || "jrh-marketing.txt";
      link.click();
      URL.revokeObjectURL(url);
    });
  });

  root.querySelectorAll("[data-mkt-share-copy]").forEach(function (button) {
    if (!navigator.share) return;
    button.hidden = false;
    button.addEventListener("click", function () {
      var card = button.closest("[data-mkt-result]");
      var source = card && card.querySelector("[id^='mkt-copy-']");
      navigator.share({
        title: button.getAttribute("data-share-title") || "Marketing IA",
        text: source ? source.textContent || "" : "",
      }).catch(function () {});
    });
  });

  if (window.JRH && window.JRH.transcribeVoice) {
    var mic = root.querySelector("[data-jrh-voice]");
    if (mic) {
      window.JRH.transcribeVoice({
        button: mic,
        form: composer,
        input: prompt,
        status: root.querySelector("[data-jrh-voice-status]"),
        unsupported: root.querySelector("[data-jrh-voice-unsupported]"),
        autoSubmit: false,
      });
    }
  }
})();
