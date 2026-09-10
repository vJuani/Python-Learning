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
          var file = new File([blob], "jrh-marketing.png", { type: "image/png" });
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

  document.querySelectorAll("[data-mkt-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      copyText(button);
    });
  });
  document.querySelectorAll("[data-mkt-share]").forEach(setupShare);
})();
