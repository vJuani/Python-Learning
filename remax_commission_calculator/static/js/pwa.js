(function () {
  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var node = byId(id);
    if (node) {
      node.textContent = value;
    }
  }

  function show(id, visible) {
    var node = byId(id);
    if (node) {
      node.hidden = !visible;
    }
  }

  function urlBase64ToUint8Array(base64String) {
    var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    var raw = window.atob(base64);
    var output = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) {
      output[i] = raw.charCodeAt(i);
    }
    return output;
  }

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function isIos() {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent || "");
  }

  function registerWorker() {
    if (!("serviceWorker" in navigator)) {
      return Promise.resolve(null);
    }
    return navigator.serviceWorker
      .register("/static/service-worker.js", { scope: "/" })
      .catch(function () {
        return null;
      });
  }

  var installPrompt = null;

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installPrompt = event;
    show("pwa-install-button", true);
    show("pwa-ios-install-help", false);
  });

  window.addEventListener("appinstalled", function () {
    installPrompt = null;
    setText("pwa-install-status", (byId("pwa-install-status") || {}).dataset.installed || "Instalado");
    show("pwa-install-button", false);
  });

  function bindInstall() {
    var status = byId("pwa-install-status");
    if (status) {
      status.textContent = isStandalone()
        ? status.dataset.installed
        : status.dataset.notInstalled;
    }
    if (isStandalone()) {
      show("pwa-install-button", false);
      show("pwa-ios-install-help", false);
    } else if (isIos()) {
      show("pwa-install-button", false);
      show("pwa-ios-install-help", true);
    }
    var button = byId("pwa-install-button");
    if (button) {
      button.addEventListener("click", function () {
        if (!installPrompt) {
          return;
        }
        installPrompt.prompt();
        installPrompt.userChoice.finally(function () {
          installPrompt = null;
          show("pwa-install-button", false);
        });
      });
    }
  }

  function currentPermission() {
    if (!("Notification" in window)) {
      return "unsupported";
    }
    return Notification.permission;
  }

  function getCurrentPushSubscription() {
    if (!("serviceWorker" in navigator) || !navigator.serviceWorker.getRegistration) {
      return Promise.resolve(null);
    }
    var lookup = navigator.serviceWorker.getRegistration("/").then(function (registration) {
      if (!registration || !registration.pushManager) {
        return null;
      }
      return registration.pushManager.getSubscription();
    }).catch(function () {
      return null;
    });
    return Promise.race([
      lookup,
      new Promise(function (resolve) {
        setTimeout(function () {
          resolve(null);
        }, 1500);
      }),
    ]);
  }

  function serverHasActive(root) {
    return Boolean(root && root.dataset.hasActive === "1");
  }

  function applyPushButtons(state) {
    show("pwa-push-enable", state === "disabled");
    show("pwa-push-test", state === "enabled");
    show("pwa-push-disable", state === "enabled");
    show("pwa-push-reset", true);
    show("pwa-push-blocked-help", state === "blocked");
    show("pwa-ios-push-help", isIos() && !isStandalone());
  }

  function fetchPushStatus() {
    return jsonFetch("/api/push/status", { credentials: "same-origin" }).catch(function () {
      return { has_active: false };
    });
  }

  function refreshPushStatus(labels, root) {
    var permission = currentPermission();
    if (permission === "unsupported") {
      setText("pwa-push-status", labels.unsupported);
      applyPushButtons(serverHasActive(root) ? "enabled" : "unsupported");
      return Promise.resolve("unsupported");
    }
    if (permission === "denied") {
      setText("pwa-push-status", labels.blocked);
      applyPushButtons("blocked");
      show("pwa-push-disable", serverHasActive(root));
      show("pwa-push-reset", true);
      return Promise.resolve("denied");
    }
    return Promise.all([getCurrentPushSubscription(), fetchPushStatus()]).then(function (results) {
      var subscription = results[0];
      var status = results[1] || {};
      var hasActive = Boolean(subscription) || Boolean(status.has_active);
      if (root) {
        root.dataset.hasActive = hasActive ? "1" : "0";
      }
      setText("pwa-push-status", hasActive ? labels.enabled : labels.disabled);
      applyPushButtons(hasActive ? "enabled" : "disabled");
      return hasActive ? "granted" : permission;
    });
  }

  function jsonFetch(url, options) {
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var body = {};
        if (text) {
          try {
            body = JSON.parse(text);
          } catch (error) {
            body = {};
          }
        }
        if (!response.ok) {
          var failure = new Error(body.error || body.message || "request_failed");
          failure.payload = body;
          throw failure;
        }
        return body;
      });
    });
  }

  function bindPush() {
    var root = byId("pwa-push-panel");
    if (!root) {
      return;
    }
    var labels = {
      enabled: root.dataset.labelEnabled || "Habilitadas",
      disabled: root.dataset.labelDisabled || "Desactivadas",
      blocked: root.dataset.labelBlocked || "Bloqueadas por el navegador",
      unsupported: root.dataset.labelUnsupported || "Este navegador no soporta notificaciones",
    };
    refreshPushStatus(labels, root);

    var enable = byId("pwa-push-enable");
    var test = byId("pwa-push-test");
    var disable = byId("pwa-push-disable");
    var reset = byId("pwa-push-reset");
    var message = byId("pwa-push-message");

    function setMessage(text, isError) {
      if (!message) {
        return;
      }
      message.textContent = text || "";
      message.classList.toggle("is-error", Boolean(isError));
    }

    if (enable) {
      enable.addEventListener("click", function () {
        if (!("Notification" in window) || !("serviceWorker" in navigator)) {
          setMessage(labels.unsupported, true);
          return;
        }
        if (isIos() && !isStandalone()) {
          setMessage(root.dataset.labelIos || "", true);
          return;
        }
        Notification.requestPermission()
          .then(function (permission) {
            refreshPushStatus(labels, root);
            if (permission !== "granted") {
              throw new Error("permission_denied");
            }
            return navigator.serviceWorker.ready;
          })
          .then(function (registration) {
            return jsonFetch("/api/push/public-key").then(function (data) {
              return registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(data.public_key),
              });
            });
          })
          .then(function (subscription) {
            var raw = subscription.toJSON();
            return jsonFetch("/api/push/subscribe", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: JSON.stringify({
                endpoint: raw.endpoint,
                keys: raw.keys,
                user_agent: navigator.userAgent,
              }),
            });
          })
          .then(function () {
            setMessage(root.dataset.labelReady || "");
            if (root) {
              root.dataset.hasActive = "1";
            }
            return refreshPushStatus(labels, root);
          })
          .catch(function (error) {
            var key = (error.payload && error.payload.error) || error.message;
            setMessage(root.dataset["err" + (key || "").replace(/_/g, "")] || key, true);
          });
      });
    }

    if (disable) {
      disable.addEventListener("click", function () {
        getCurrentPushSubscription()
          .then(function (subscription) {
            if (!subscription) {
              return { ok: true };
            }
            var endpoint = subscription.endpoint;
            return subscription.unsubscribe().then(function () {
              return jsonFetch("/api/push/unsubscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify({ endpoint: endpoint }),
              });
            });
          })
          .then(function () {
            if (root) {
              root.dataset.hasActive = "0";
            }
            setText("pwa-push-status", labels.disabled);
            applyPushButtons("disabled");
            setMessage(root.dataset.labelDisabledOk || "");
          })
          .catch(function (error) {
            var key = (error.payload && error.payload.error) || error.message;
            setMessage(root.dataset.labelDisableFailed || key, true);
            refreshPushStatus(labels, root);
          });
      });
    }

    if (reset) {
      reset.addEventListener("click", function () {
        getCurrentPushSubscription()
          .then(function (subscription) {
            if (!subscription) {
              return true;
            }
            return subscription.unsubscribe();
          })
          .then(function () {
            return jsonFetch("/api/push/reset", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "same-origin",
              body: "{}",
            });
          })
          .then(function () {
            if (root) {
              root.dataset.hasActive = "0";
            }
            setText("pwa-push-status", labels.disabled);
            applyPushButtons("disabled");
            setMessage(root.dataset.labelResetOk || "");
          })
          .catch(function (error) {
            var key = (error.payload && error.payload.error) || error.message;
            setMessage(root.dataset.labelResetFailed || key, true);
            refreshPushStatus(labels, root);
          });
      });
    }

    if (test) {
      test.addEventListener("click", function () {
        jsonFetch("/api/push/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: "{}",
        })
          .then(function () {
            setMessage(root.dataset.labelTestSent || "");
          })
          .catch(function (error) {
            var key = (error.payload && error.payload.error) || error.message;
            setMessage(key, true);
          });
      });
    }
  }

  registerWorker();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      bindInstall();
      bindPush();
    });
  } else {
    bindInstall();
    bindPush();
  }
}());
