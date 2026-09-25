/* JRH One PWA service worker. Caches static assets only. Never caches private data. */
const CACHE_NAME = "jrh-one-static-v7";
const STATIC_PREFIXES = [
  "/static/css/",
  "/static/js/",
  "/static/icons/",
  "/static/brand/",
  "/static/manifest.webmanifest",
];
const NEVER_CACHE_PREFIXES = [
  "/api/",
  "/operations",
  "/operation",
  "/billing",
  "/invoices",
  "/invoice",
  "/cash",
  "/treasury",
  "/reports",
  "/wallet",
  "/my-wallet",
  "/agent-account",
  "/settings/arca",
];

function sameOrigin(url) {
  return url.origin === self.location.origin;
}

function isStaticAsset(pathname) {
  return STATIC_PREFIXES.some(function (prefix) {
    return pathname === prefix || pathname.startsWith(prefix);
  });
}

function isPrivatePath(pathname) {
  return NEVER_CACHE_PREFIXES.some(function (prefix) {
    return pathname === prefix || pathname.startsWith(prefix);
  });
}

function safeInternalUrl(raw) {
  var value = String(raw || "/").trim() || "/";
  if (value.charAt(0) !== "/" || value.indexOf("//") === 0) {
    return "/";
  }
  if (value.indexOf("://") !== -1) {
    return "/";
  }
  return value;
}

self.addEventListener("install", function (event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll([
        "/static/icons/icon-192.png",
        "/static/icons/icon-512.png",
        "/static/manifest.webmanifest",
      ]).catch(function () {
        return undefined;
      });
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) {
          return key !== CACHE_NAME;
        }).map(function (key) {
          return caches.delete(key);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") {
    return;
  }
  var url;
  try {
    url = new URL(request.url);
  } catch (error) {
    return;
  }
  if (!sameOrigin(url)) {
    return;
  }
  if (isPrivatePath(url.pathname) || url.pathname.indexOf("/api/") === 0) {
    event.respondWith(fetch(request));
    return;
  }
  if (url.pathname === "/static/js/pwa.js") {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(request);
      })
    );
    return;
  }
  if (url.pathname.indexOf("/static/css/") === 0) {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.ok && response.type === "basic") {
          var copy = response.clone();
          caches.open(CACHE_NAME).then(function (cache) {
            cache.put(request, copy);
          });
        }
        return response;
      }).catch(function () {
        return caches.match(request);
      })
    );
    return;
  }
  if (isStaticAsset(url.pathname)) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        if (cached) {
          return cached;
        }
        return fetch(request).then(function (response) {
          if (response && response.ok && response.type === "basic") {
            var copy = response.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(request, copy);
            });
          }
          return response;
        });
      })
    );
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(request);
      })
    );
  }
});

self.addEventListener("push", function (event) {
  var payload = {
    title: "JRH One",
    body: "",
    url: "/",
    tag: "jrh-one",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
  };
  if (event.data) {
    try {
      var parsed = event.data.json();
      payload.title = parsed.title || payload.title;
      payload.body = parsed.body || "";
      payload.url = parsed.notification_id
        ? "/notifications/" + parsed.notification_id + "/open"
        : safeInternalUrl(parsed.url);
      payload.tag = parsed.tag || payload.tag;
      payload.icon = parsed.icon || payload.icon;
      payload.badge = parsed.badge || payload.badge;
      payload.type = parsed.type || "";
      payload.priority = parsed.priority || "info";
      payload.notification_id = parsed.notification_id || null;
      payload.unread_count = unreadCountFrom(parsed.unread_count);
    } catch (error) {
      payload.body = event.data.text() || "";
    }
  }
  // iOS revokes subscriptions whose pushes do not show a notification, so
  // showNotification always runs, even if badge or postMessage fail.
  event.waitUntil(
    Promise.all([
      self.registration.showNotification(payload.title, {
        body: payload.body,
        icon: payload.icon,
        badge: payload.badge,
        tag: payload.tag,
        data: {
          url: payload.url,
          type: payload.type,
          priority: payload.priority,
          notification_id: payload.notification_id,
        },
      }),
      updateAppBadge(payload.unread_count),
      broadcastToClients(notificationReceivedMessage(payload)),
    ])
  );
});

function unreadCountFrom(raw) {
  var value = parseInt(raw, 10);
  return isNaN(value) || value < 0 ? null : value;
}

function notificationReceivedMessage(payload) {
  return {
    type: "notification-received",
    unread_count: payload.unread_count === undefined ? null : payload.unread_count,
    notification_id: payload.notification_id || null,
    notification_type: payload.type || "",
  };
}

function updateAppBadge(count) {
  var nav = self.navigator;
  if (count === null || count === undefined || !nav) {
    return Promise.resolve();
  }
  try {
    if (count > 0 && nav.setAppBadge) {
      return Promise.resolve(nav.setAppBadge(count)).catch(function () {
        return undefined;
      });
    }
    if (count === 0 && nav.clearAppBadge) {
      return Promise.resolve(nav.clearAppBadge()).catch(function () {
        return undefined;
      });
    }
  } catch (error) {
    return Promise.resolve();
  }
  return Promise.resolve();
}

function broadcastToClients(message) {
  return self.clients
    .matchAll({ type: "window", includeUncontrolled: true })
    .then(function (clients) {
      clients.forEach(function (client) {
        client.postMessage(message);
      });
    })
    .catch(function () {
      return undefined;
    });
}

function urlBase64ToUint8Array(base64String) {
  var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  var raw = self.atob(base64);
  var output = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i);
  }
  return output;
}

function applicationServerKeyFor(oldSubscription) {
  var options = oldSubscription && oldSubscription.options;
  if (options && options.applicationServerKey) {
    return Promise.resolve(options.applicationServerKey);
  }
  return fetch("/api/push/public-key", { credentials: "include" })
    .then(function (response) {
      return response.ok ? response.json() : null;
    })
    .then(function (data) {
      return data && data.public_key ? urlBase64ToUint8Array(data.public_key) : null;
    });
}

// Chrome/Firefox rotate subscriptions and announce it here; Safari does not
// fire this event, so pwa.js also resyncs whenever the app is opened.
self.addEventListener("pushsubscriptionchange", function (event) {
  var oldSubscription = event.oldSubscription || null;
  var oldJson = oldSubscription && oldSubscription.toJSON ? oldSubscription.toJSON() : {};
  var oldEndpoint = (oldSubscription && oldSubscription.endpoint) || "";
  var oldAuth = (oldJson.keys && oldJson.keys.auth) || "";
  var resubscribe = event.newSubscription
    ? Promise.resolve(event.newSubscription)
    : applicationServerKeyFor(oldSubscription).then(function (key) {
        if (!key) {
          return null;
        }
        return self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key,
        });
      });
  event.waitUntil(
    resubscribe
      .then(function (subscription) {
        if (!subscription || !oldEndpoint) {
          return undefined;
        }
        return fetch("/api/push/resubscribe", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            old_endpoint: oldEndpoint,
            old_auth: oldAuth,
            subscription: subscription.toJSON(),
          }),
        });
      })
      .catch(function () {
        return undefined;
      })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = safeInternalUrl(
    event.notification && event.notification.data && event.notification.data.url
  );
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i += 1) {
        var client = clients[i];
        if ("focus" in client) {
          if (client.url && client.url.indexOf(self.location.origin) === 0) {
            if ("navigate" in client && target !== "/") {
              return client.navigate(target).then(function (opened) {
                return opened && opened.focus ? opened.focus() : client.focus();
              });
            }
            return client.focus();
          }
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(target);
      }
      return undefined;
    })
  );
});
