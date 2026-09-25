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

  function fetchDeviceStatus(subscription) {
    if (!subscription) {
      return Promise.resolve({ device_active: false });
    }
    return jsonFetch("/api/push/device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    }).catch(function () {
      return { device_active: false };
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
    return getCurrentPushSubscription().then(function (subscription) {
      return fetchDeviceStatus(subscription);
    }).then(function (status) {
      var hasActive = Boolean(status && status.device_active);
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

  var DEVICE_STORE_KEY = "jrh_push_device";
  var PROMPT_STORE_KEY = "jrh_push_prompt_dismissed_at";
  var PROMPT_SNOOZE_MS = 14 * 24 * 60 * 60 * 1000;

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      if (value === null) {
        window.localStorage.removeItem(key);
      } else {
        window.localStorage.setItem(key, value);
      }
    } catch (error) {
      /* private mode: preference simply is not remembered */
    }
  }

  function rememberSubscription(subscription) {
    if (!subscription) {
      return;
    }
    var raw = subscription.toJSON ? subscription.toJSON() : {};
    storageSet(
      DEVICE_STORE_KEY,
      JSON.stringify({
        endpoint: subscription.endpoint,
        auth: (raw.keys && raw.keys.auth) || "",
      })
    );
  }

  function rememberedSubscription() {
    try {
      var parsed = JSON.parse(storageGet(DEVICE_STORE_KEY) || "null");
      return parsed && parsed.endpoint ? parsed : null;
    } catch (error) {
      return null;
    }
  }

  function forgetSubscription() {
    storageSet(DEVICE_STORE_KEY, null);
  }

  function pushSubscribe(registration) {
    return jsonFetch("/api/push/public-key").then(function (data) {
      return registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(data.public_key),
      });
    });
  }

  function saveSubscription(subscription) {
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
    }).then(function (result) {
      rememberSubscription(subscription);
      return result;
    });
  }

  // Shared by the settings panel and the soft prompt. Rejects with
  // Error("ios_install_required"), ("unsupported") or ("permission_denied").
  function enablePushOnThisDevice() {
    if (isIos() && !isStandalone()) {
      return Promise.reject(new Error("ios_install_required"));
    }
    if (!("Notification" in window) || !("serviceWorker" in navigator)) {
      return Promise.reject(new Error("unsupported"));
    }
    return Notification.requestPermission()
      .then(function (permission) {
        if (permission !== "granted") {
          throw new Error("permission_denied");
        }
        return navigator.serviceWorker.ready;
      })
      .then(pushSubscribe)
      .then(saveSubscription);
  }

  function resubscribe(previous, subscription) {
    return jsonFetch("/api/push/resubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        old_endpoint: previous.endpoint,
        old_auth: previous.auth || "",
        subscription: subscription.toJSON(),
      }),
    }).then(function (result) {
      rememberSubscription(subscription);
      return result;
    });
  }

  // Safari/iOS never fires pushsubscriptionchange, so every app open
  // compares the browser subscription with the one this device registered.
  // A device the user (or a logout) deactivated is never reactivated here.
  function resyncSubscription() {
    if (currentPermission() !== "granted") {
      return Promise.resolve(null);
    }
    var previous = rememberedSubscription();
    return getCurrentPushSubscription()
      .then(function (subscription) {
        if (subscription) {
          if (previous && previous.endpoint !== subscription.endpoint) {
            return resubscribe(previous, subscription);
          }
          if (!previous) {
            rememberSubscription(subscription);
          }
          return null;
        }
        if (!previous) {
          return null;
        }
        return fetchDeviceStatus({ endpoint: previous.endpoint }).then(function (status) {
          if (!status || !status.device_active) {
            return null;
          }
          return navigator.serviceWorker.ready
            .then(pushSubscribe)
            .then(function (fresh) {
              return resubscribe(previous, fresh);
            });
        });
      })
      .catch(function () {
        return null;
      });
  }

  function promptSnoozed() {
    var raw = parseInt(storageGet(PROMPT_STORE_KEY) || "0", 10);
    return Boolean(raw) && Date.now() - raw < PROMPT_SNOOZE_MS;
  }

  function snoozePrompt() {
    storageSet(PROMPT_STORE_KEY, String(Date.now()));
  }

  function bindSoftPrompt() {
    var prompt = byId("push-soft-prompt");
    if (!prompt || promptSnoozed()) {
      return;
    }
    var permission = currentPermission();
    var iosBrowser = isIos() && !isStandalone();
    if (!iosBrowser && (permission === "denied" || permission === "unsupported")) {
      return;
    }
    var accept = byId("push-soft-prompt-accept");
    var later = byId("push-soft-prompt-later");
    var text = byId("push-soft-prompt-text");
    var message = byId("push-soft-prompt-message");

    function close() {
      prompt.hidden = true;
    }

    getCurrentPushSubscription()
      .then(fetchDeviceStatus)
      .then(function (status) {
        if (status && status.device_active) {
          return;
        }
        if (iosBrowser) {
          if (text) {
            text.textContent = prompt.dataset.labelIos || "";
          }
          if (accept) {
            accept.hidden = true;
          }
        }
        prompt.hidden = false;
      });

    if (later) {
      later.addEventListener("click", function () {
        snoozePrompt();
        close();
      });
    }
    if (accept) {
      accept.addEventListener("click", function () {
        accept.disabled = true;
        enablePushOnThisDevice()
          .then(function () {
            close();
          })
          .catch(function (error) {
            accept.disabled = false;
            if (error && error.message === "permission_denied") {
              snoozePrompt();
              close();
              return;
            }
            if (message) {
              message.hidden = false;
              message.textContent = prompt.dataset.labelFailed || "";
            }
          });
      });
    }
  }

  function bindDevices(onCurrentDeactivated) {
    var list = byId("push-devices");
    if (!list) {
      return;
    }
    var status = byId("push-devices-message");
    function say(text, isError) {
      if (status) {
        status.textContent = text || "";
        status.classList.toggle("is-error", Boolean(isError));
      }
    }
    function markInactive(item) {
      item.classList.remove("is-active");
      item.classList.add("is-inactive");
      var state = item.querySelector("[data-device-state]");
      if (state) {
        state.textContent = list.dataset.labelInactive || "";
      }
      var button = item.querySelector("[data-device-deactivate]");
      if (button) {
        button.remove();
      }
    }
    list.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("[data-device-deactivate]") : null;
      if (!button) {
        return;
      }
      var item = button.closest("[data-device-id]");
      button.disabled = true;
      jsonFetch("/api/push/devices/" + encodeURIComponent(item.dataset.deviceId) + "/deactivate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: "{}",
      })
        .then(function () {
          markInactive(item);
          say(list.dataset.labelDeactivated || "");
          if (item.dataset.current === "1" && onCurrentDeactivated) {
            onCurrentDeactivated();
          }
        })
        .catch(function () {
          button.disabled = false;
          say(list.dataset.labelFailed || "", true);
        });
    });
    var all = byId("push-devices-deactivate-all");
    if (all) {
      all.addEventListener("click", function () {
        if (!window.confirm(all.dataset.confirm || "")) {
          return;
        }
        all.disabled = true;
        getCurrentPushSubscription()
          .then(function (subscription) {
            return subscription ? subscription.unsubscribe() : true;
          })
          .catch(function () {
            return true;
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
            forgetSubscription();
            Array.prototype.forEach.call(list.querySelectorAll("[data-device-id]"), markInactive);
            say(list.dataset.labelAllDeactivated || "");
            if (onCurrentDeactivated) {
              onCurrentDeactivated();
            }
          })
          .catch(function () {
            say(list.dataset.labelFailed || "", true);
          })
          .then(function () {
            all.disabled = false;
          });
      });
    }
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
    bindDevices(function () {
      root.dataset.hasActive = "0";
      refreshPushStatus(labels, root);
    });

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
        enablePushOnThisDevice()
          .then(function () {
            setMessage(root.dataset.labelReady || "");
            root.dataset.hasActive = "1";
            return refreshPushStatus(labels, root);
          })
          .catch(function (error) {
            var key = (error.payload && error.payload.error) || error.message;
            if (key === "ios_install_required") {
              setMessage(root.dataset.labelIos || "", true);
              return;
            }
            if (key === "unsupported") {
              setMessage(labels.unsupported, true);
              return;
            }
            refreshPushStatus(labels, root);
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
            forgetSubscription();
            snoozePrompt();
            root.dataset.hasActive = "0";
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
            forgetSubscription();
            snoozePrompt();
            root.dataset.hasActive = "0";
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

  function isLogoutLink(anchor) {
    if (!anchor || !anchor.href) {
      return false;
    }
    try {
      var url = new URL(anchor.href, window.location.href);
      return url.origin === window.location.origin && url.pathname === "/logout";
    } catch (error) {
      return false;
    }
  }

  function bindLogout() {
    document.addEventListener("click", function (event) {
      var anchor = event.target && event.target.closest ? event.target.closest("a") : null;
      if (!isLogoutLink(anchor) || event.defaultPrevented) {
        return;
      }
      event.preventDefault();
      var target = anchor.href;
      var done = false;
      function leave() {
        if (!done) {
          done = true;
          window.location.href = target;
        }
      }
      setTimeout(leave, 2000);
      getCurrentPushSubscription()
        .then(function (subscription) {
          if (!subscription) {
            return null;
          }
          return fetch("/api/push/unsubscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            keepalive: true,
            body: JSON.stringify({ endpoint: subscription.endpoint }),
          });
        })
        .catch(function () {
          return null;
        })
        .then(leave);
    });
  }

  function pushEligible() {
    return Boolean(document.querySelector("[data-push-eligible]"));
  }

  function boot() {
    bindInstall();
    bindPush();
    if (pushEligible()) {
      resyncSubscription().then(bindSoftPrompt);
    }
  }

  registerWorker();
  bindLogout();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}());
