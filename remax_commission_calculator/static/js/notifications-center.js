(function () {
  var root = document.getElementById("notification-center");
  if (!root) {
    return;
  }
  var feed = document.getElementById("notif-feed");
  var empty = document.getElementById("notif-empty");
  var more = document.getElementById("notif-load-more");
  var moreMessage = document.getElementById("notif-more-message");
  var banner = document.getElementById("notif-live-banner");
  var markAll = document.querySelector("[data-mark-all]");
  var categoryForm = document.querySelector("[data-category-form]");
  var labels = root.dataset;
  var BUCKETS = ["today", "yesterday", "older"];

  function live() {
    return window.JRHNotifications || null;
  }

  function applyUnread(count) {
    if (typeof count !== "number") {
      return;
    }
    if (live()) {
      live().setUnread(count);
    }
    if (markAll) {
      markAll.hidden = count === 0;
    }
  }

  function postJson(url) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: "{}",
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("request_failed");
      }
      return response.json();
    });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function buildCard(item) {
    var card = el(
      "article",
      "notif-card notif-card--" + (item.priority || "info") + (item.is_read ? "" : " is-unread")
    );
    card.dataset.notificationId = item.id;
    var link = el("a", "notif-card__link");
    link.href = item.open_url;
    var icon = el("span", "notif-card__icon", item.icon || "");
    icon.setAttribute("aria-hidden", "true");
    var copy = el("span", "notif-card__copy");
    var top = el("span", "notif-card__top");
    top.appendChild(el("span", "notif-card__title", item.title));
    var time = el("time", "notif-card__time", item.time_label);
    time.setAttribute("datetime", item.created_at || "");
    time.title = item.when_label || "";
    top.appendChild(time);
    copy.appendChild(top);
    if (item.body) {
      copy.appendChild(el("span", "notif-card__body", item.body));
    }
    if (item.reason) {
      copy.appendChild(el("span", "notif-card__meta", labels.labelReason + ": " + item.reason));
    }
    if (item.priority === "important" || item.priority === "urgent") {
      copy.appendChild(
        el(
          "span",
          "notif-card__priority notif-card__priority--" + item.priority,
          item.priority === "urgent" ? labels.labelUrgent : labels.labelImportant
        )
      );
    }
    var dot = el("span", "notif-card__dot");
    dot.setAttribute("aria-label", labels.labelUnread || "");
    dot.hidden = Boolean(item.is_read);
    link.appendChild(icon);
    link.appendChild(copy);
    link.appendChild(dot);
    card.appendChild(link);
    if (!item.is_read) {
      var form = el("form", "notif-card__read");
      form.method = "post";
      form.action = "/notifications/" + item.id + "/read";
      form.setAttribute("data-mark-read", "");
      var button = el("button", "notif-card__read-btn", "\u2713");
      button.type = "submit";
      button.title = labels.labelMarkRead || "";
      button.setAttribute("aria-label", labels.labelMarkRead || "");
      form.appendChild(button);
      card.appendChild(form);
    }
    return card;
  }

  function groupFor(bucket) {
    var existing = feed.querySelector('[data-day-bucket="' + bucket + '"] .notif-group__items');
    if (existing) {
      return existing;
    }
    var group = el("div", "notif-group");
    group.dataset.dayBucket = bucket;
    var key = "label" + bucket.charAt(0).toUpperCase() + bucket.slice(1);
    group.appendChild(el("p", "notif-group__heading", labels[key] || bucket));
    var items = el("div", "notif-group__items");
    group.appendChild(items);
    var order = BUCKETS.indexOf(bucket);
    var next = null;
    Array.prototype.some.call(feed.querySelectorAll("[data-day-bucket]"), function (node) {
      if (BUCKETS.indexOf(node.dataset.dayBucket) > order) {
        next = node;
        return true;
      }
      return false;
    });
    feed.insertBefore(group, next);
    return items;
  }

  function markCardRead(card) {
    card.classList.remove("is-unread");
    var dot = card.querySelector(".notif-card__dot");
    if (dot) {
      dot.hidden = true;
    }
    var form = card.querySelector("[data-mark-read]");
    if (form) {
      form.remove();
    }
    if (root.dataset.filter === "unread") {
      var group = card.closest(".notif-group");
      card.remove();
      if (group && !group.querySelector(".notif-card")) {
        group.remove();
      }
      if (!feed.querySelector(".notif-card") && more.hidden) {
        empty.hidden = false;
      }
    }
  }

  feed.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches || !form.matches("[data-mark-read]")) {
      return;
    }
    event.preventDefault();
    var card = form.closest("[data-notification-id]");
    var button = form.querySelector("button");
    if (button) {
      button.disabled = true;
    }
    postJson("/api/notifications/" + encodeURIComponent(card.dataset.notificationId) + "/read")
      .then(function (data) {
        markCardRead(card);
        applyUnread(data.unread_count);
      })
      .catch(function () {
        form.submit();
      });
  });

  if (markAll) {
    markAll.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = markAll.querySelector("button");
      if (button) {
        button.disabled = true;
      }
      postJson("/api/notifications/read-all")
        .then(function (data) {
          Array.prototype.forEach.call(feed.querySelectorAll(".notif-card.is-unread"), markCardRead);
          applyUnread(data.unread_count);
        })
        .catch(function () {
          markAll.submit();
        })
        .then(function () {
          if (button) {
            button.disabled = false;
          }
        });
    });
  }

  if (categoryForm) {
    var select = categoryForm.querySelector("select");
    if (select) {
      select.addEventListener("change", function () {
        categoryForm.submit();
      });
    }
  }

  function loadMore() {
    var before = more.dataset.before;
    if (!before) {
      more.hidden = true;
      return;
    }
    var params = new URLSearchParams({ before: before, limit: labels.limit || "20" });
    if (root.dataset.filter === "unread") {
      params.set("unread_only", "1");
    }
    if (root.dataset.category) {
      params.set("category", root.dataset.category);
    }
    more.disabled = true;
    moreMessage.textContent = "";
    fetch("/api/notifications?" + params.toString(), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("request_failed");
        }
        return response.json();
      })
      .then(function (data) {
        (data.items || []).forEach(function (item) {
          groupFor(item.day_bucket || "older").appendChild(buildCard(item));
        });
        more.dataset.before = data.next_before || "";
        more.hidden = !data.has_more;
        empty.hidden = Boolean(feed.querySelector(".notif-card"));
        applyUnread(data.unread_count);
      })
      .catch(function () {
        moreMessage.textContent = labels.labelFailed || "";
      })
      .then(function () {
        more.disabled = false;
      });
  }

  if (more) {
    more.addEventListener("click", loadMore);
  }

  document.addEventListener("jrh:notification-received", function () {
    if (banner) {
      banner.hidden = false;
    }
  });

  document.addEventListener("jrh:unread-count", function (event) {
    if (markAll && event.detail && typeof event.detail.unread_count === "number") {
      markAll.hidden = event.detail.unread_count === 0;
    }
  });
}());
