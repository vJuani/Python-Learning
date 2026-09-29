(function () {
    var root = document.querySelector("[data-property-photos]");
    if (!root) {
        return;
    }
    var input = root.querySelector("[data-photo-input]");
    var upload = root.querySelector("[data-photo-upload]");
    if (input && upload) {
        input.addEventListener("change", function () {
            if (input.files && input.files.length) {
                upload.submit();
            }
        });
    }
    root.querySelectorAll("[data-photo-delete]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            var message = form.getAttribute("data-confirm") || "";
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
    var list = root.querySelector("[data-photo-list]");
    var orderForm = root.querySelector("[data-photo-order]");
    if (!list || !orderForm) {
        return;
    }
    var dragged = null;
    list.addEventListener("dragstart", function (event) {
        var item = event.target.closest("[data-media-id]");
        if (!item) {
            return;
        }
        dragged = item;
        event.dataTransfer.effectAllowed = "move";
    });
    list.addEventListener("dragover", function (event) {
        if (!dragged) {
            return;
        }
        event.preventDefault();
        var item = event.target.closest("[data-media-id]");
        if (!item || item === dragged) {
            return;
        }
        var rect = item.getBoundingClientRect();
        var after = event.clientX > rect.left + rect.width / 2;
        list.insertBefore(dragged, after ? item.nextSibling : item);
    });
    list.addEventListener("drop", function (event) {
        event.preventDefault();
        if (!dragged) {
            return;
        }
        var ids = Array.prototype.map.call(
            list.querySelectorAll("[data-media-id]"),
            function (node) {
                return node.getAttribute("data-media-id");
            }
        );
        orderForm.querySelector("[name=order]").value = ids.join(",");
        orderForm.submit();
    });
})();
