(function () {
  function copyText(button) {
    var targetId = button.getAttribute("data-copy-target");
    var node = targetId ? document.getElementById(targetId) : null;
    if (!node) return;
    var text = node.textContent || "";
    var done = button.getAttribute("data-copied") || "OK";
    function mark() {
      button.textContent = done;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(mark).catch(function () {
        mark();
      });
      return;
    }
    mark();
  }

  function canShareFiles() {
    try {
      return !!(navigator.share && navigator.canShare);
    } catch (error) {
      return false;
    }
  }

  function setupShare(button) {
    if (!navigator.share) return;
    button.hidden = false;
    button.addEventListener("click", function () {
      var title = button.getAttribute("data-share-title") || "JRH";
      var url = button.getAttribute("data-share-url");
      fetch(url, { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) throw new Error("share");
          return response.blob();
        })
        .then(function (blob) {
          var file = new File([blob], button.getAttribute("data-share-name") || "jrh-content.png", { type: "image/png" });
          var payload = { title: title, files: [file] };
          if (canShareFiles() && navigator.canShare(payload)) {
            return navigator.share(payload);
          }
          return navigator.share({ title: title, url: url });
        })
        .catch(function () {
          if (url) window.location.href = url;
        });
    });
  }

  function setupChips(root) {
    var form = root.closest("form") || document.querySelector("[data-mkt-prompt]");
    var field = form ? form.querySelector("textarea[name='prompt']") : null;
    if (!field) return;
    root.querySelectorAll("[data-chip]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var extra = chip.getAttribute("data-chip") || "";
        if (chip.getAttribute("data-chip-replace") === "1") {
          field.value = extra;
          return;
        }
        var current = (field.value || "").trim();
        if (current.indexOf(extra) !== -1) return;
        field.value = current ? current + ", " + extra : extra;
      });
    });
  }

  function setupPromptGuard(form) {
    var keyField = form.querySelector("[name='idempotency_key']");
    if (keyField && !keyField.value) {
      keyField.value = "mkt-" + String(Date.now()) + "-" + Math.random().toString(16).slice(2);
    }
    var submitting = false;
    form.addEventListener("submit", function (event) {
      if (submitting) {
        event.preventDefault();
        return;
      }
      submitting = true;
      var button = form.querySelector("button[type='submit']");
      if (button) button.disabled = true;
    });
  }

  function renderItem(article, item) {
    if (!article || !item) return;
    article.setAttribute("data-status", item.pipeline_status || "");
    var frame = article.querySelector("[data-mkt-frame]");
    if (!frame) return;
    if (item.ready && item.preview_url) {
      if (frame.querySelector("img")) return;
      frame.innerHTML =
        '<a href="/marketing/assets/' +
        item.id +
        '"><img src="' +
        item.preview_url +
        '" alt=""></a>';
      return;
    }
    if (item.failed) {
      frame.innerHTML =
        '<div class="mkt-fail"><p>No pude generar esta variante.</p>' +
        '<form method="post" action="/marketing/assets/' +
        item.id +
        '/retry"><button type="submit" class="btn btn-secondary">Reintentar</button></form></div>';
    }
  }

  function pollBatch(root) {
    if (!root || root.getAttribute("data-mkt-done") === "1") return;
    var url = root.getAttribute("data-mkt-status-url");
    if (!url) return;
    var mark = root.querySelector("[data-creating-mark]");
    function tick() {
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (response) {
          if (!response.ok) throw new Error("status");
          return response.json();
        })
        .then(function (payload) {
          (payload.items || []).forEach(function (item) {
            renderItem(document.querySelector('[data-mkt-item="' + item.id + '"]'), item);
          });
          if (!payload.creating) {
            root.setAttribute("data-mkt-done", "1");
            if (mark) mark.textContent = "✓";
            var finish = root.querySelector('[data-step="finish"]');
            if (finish) finish.textContent = finish.textContent.replace("○", "✓");
            window.setTimeout(function () {
              window.location.reload();
            }, 400);
            return;
          }
          window.setTimeout(tick, 1200);
        })
        .catch(function () {
          window.setTimeout(tick, 2000);
        });
    }
    tick();
  }

  function setupCreateDialog() {
    var dialog = document.querySelector("[data-mkt-dialog]");
    if (!dialog) return;
    document.querySelectorAll("[data-mkt-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (typeof dialog.showModal === "function") {
          dialog.showModal();
        } else {
          dialog.setAttribute("open", "open");
        }
      });
    });
    dialog.querySelectorAll("[data-mkt-close]").forEach(function (button) {
      button.addEventListener("click", function () {
        dialog.close();
      });
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) {
        dialog.close();
      }
    });
  }

  document.querySelectorAll("[data-mkt-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      copyText(button);
    });
  });
  document.querySelectorAll("[data-mkt-share]").forEach(setupShare);
  document.querySelectorAll("[data-mkt-chips]").forEach(setupChips);
  document.querySelectorAll("[data-mkt-prompt]").forEach(setupPromptGuard);
  document.querySelectorAll("[data-mkt-batch]").forEach(pollBatch);
  setupCreateDialog();
})();
