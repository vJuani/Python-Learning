(function () {
  var REFRESH_MIN_INTERVAL_MS = 5000;
  var lastRefreshAt = 0;
  var inflight = null;

  function badges() {
    return document.querySelectorAll("[data-unread-badge]");
  }

  function formatCount(count) {
    return count > 99 ? "99+" : String(count);
  }

  function updateAppBadge(count) {
    var nav = window.navigator;
    try {
      if (count > 0 && nav.setAppBadge) {
        nav.setAppBadge(count).catch(function () {});
      } else if (count === 0 && nav.clearAppBadge) {
        nav.clearAppBadge().catch(function () {});
      }
    } catch (error) {
      /* Badging API not available */
    }
  }

  function setUnread(count) {
    var value = parseInt(count, 10);
    if (isNaN(value) || value < 0) {
      return;
    }
    Array.prototype.forEach.call(badges(), function (node) {
      node.textContent = formatCount(value);
      node.hidden = value === 0;
    });
    updateAppBadge(value);
    document.dispatchEvent(
      new CustomEvent("jrh:unread-count", { detail: { unread_count: value } })
    );
  }

  function refresh(force) {
    if (!badges().length) {
      return Promise.resolve(null);
    }
    if (inflight) {
      return inflight;
    }
    if (!force && Date.now() - lastRefreshAt < REFRESH_MIN_INTERVAL_MS) {
      return Promise.resolve(null);
    }
    lastRefreshAt = Date.now();
    inflight = fetch("/api/notifications/unread-count", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (data && typeof data.unread_count === "number") {
          setUnread(data.unread_count);
          return data.unread_count;
        }
        return null;
      })
      .catch(function () {
        return null;
      })
      .then(function (value) {
        inflight = null;
        return value;
      });
    return inflight;
  }

  function onWorkerMessage(event) {
    var data = event && event.data;
    if (!data || data.type !== "notification-received") {
      return;
    }
    if (typeof data.unread_count === "number") {
      setUnread(data.unread_count);
    } else {
      refresh(true);
    }
    document.dispatchEvent(new CustomEvent("jrh:notification-received", { detail: data }));
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", onWorkerMessage);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      refresh(false);
    }
  });

  window.addEventListener("focus", function () {
    refresh(false);
  });

  document.addEventListener(
    "toggle",
    function (event) {
      var target = event.target;
      if (target && target.classList && target.classList.contains("nav-bell-dropdown") && target.open) {
        refresh(true);
      }
    },
    true
  );

  window.JRHNotifications = {
    setUnread: setUnread,
    refresh: refresh,
    formatCount: formatCount,
  };

  function boot() {
    refresh(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}());
