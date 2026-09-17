(function () {
  var root = document.querySelector("[data-mkt-chat]");
  if (!root) return;

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

  var openPicker = root.querySelector("[data-mkt-open-picker]");
  if (openPicker && picker) {
    openPicker.addEventListener("click", function () {
      if (typeof picker.showModal === "function") picker.showModal();
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
    });
  }

  document.addEventListener("click", function (event) {
    if (!document.body.classList.contains("is-mkt-history-open")) return;
    if (sidebar && sidebar.contains(event.target)) return;
    if (historyBtn && historyBtn.contains(event.target)) return;
    document.body.classList.remove("is-mkt-history-open");
  });

  if (composer) {
    composer.addEventListener("submit", function () {
      if (generating) generating.hidden = false;
    });
  }

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
