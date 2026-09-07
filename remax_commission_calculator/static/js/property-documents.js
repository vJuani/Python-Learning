(function () {
    function wireBrochureDialog() {
        var dialog = document.querySelector("[data-brochure-dialog]");
        var openButton = document.querySelector("[data-brochure-open]");
        if (!dialog || !openButton || !dialog.showModal) {
            return;
        }
        openButton.addEventListener("click", function () {
            dialog.showModal();
        });
        dialog.querySelectorAll("[data-brochure-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                dialog.close();
            });
        });
    }

    function moveItem(items, from, to) {
        if (to < 0 || to >= items.length) {
            return items;
        }
        var clone = items.slice();
        var moved = clone.splice(from, 1)[0];
        clone.splice(to, 0, moved);
        return clone;
    }

    function wireUploadForm() {
        var form = document.querySelector("[data-property-doc-form]");
        var input = document.querySelector("[data-property-doc-input]");
        var list = document.querySelector("[data-property-doc-previews]");
        if (!form || !input || !list) {
            return;
        }

        var files = [];

        function syncInput() {
            var transfer = new DataTransfer();
            files.forEach(function (file) {
                transfer.items.add(file);
            });
            input.files = transfer.files;
        }

        function render() {
            list.innerHTML = "";
            if (!files.length) {
                list.hidden = true;
                return;
            }
            list.hidden = false;
            files.forEach(function (file, index) {
                var item = document.createElement("li");
                item.className = "property-doc-preview";
                var preview = document.createElement(file.type.indexOf("image/") === 0 ? "img" : "span");
                if (preview.tagName === "IMG") {
                    preview.alt = file.name;
                    preview.src = URL.createObjectURL(file);
                } else {
                    preview.textContent = file.name;
                }
                var meta = document.createElement("div");
                meta.className = "property-doc-preview__meta";
                var name = document.createElement("strong");
                name.textContent = (index + 1) + ". " + file.name;
                meta.appendChild(name);

                var actions = document.createElement("div");
                actions.className = "property-doc-preview__actions";

                var up = document.createElement("button");
                up.type = "button";
                up.className = "btn btn-small btn-secondary";
                up.textContent = "↑";
                up.addEventListener("click", function () {
                    files = moveItem(files, index, index - 1);
                    syncInput();
                    render();
                });

                var down = document.createElement("button");
                down.type = "button";
                down.className = "btn btn-small btn-secondary";
                down.textContent = "↓";
                down.addEventListener("click", function () {
                    files = moveItem(files, index, index + 1);
                    syncInput();
                    render();
                });

                var remove = document.createElement("button");
                remove.type = "button";
                remove.className = "btn btn-small btn-secondary";
                remove.textContent = "×";
                remove.addEventListener("click", function () {
                    files.splice(index, 1);
                    syncInput();
                    render();
                });

                actions.appendChild(up);
                actions.appendChild(down);
                actions.appendChild(remove);
                item.appendChild(preview);
                item.appendChild(meta);
                item.appendChild(actions);
                list.appendChild(item);
            });
        }

        input.addEventListener("change", function () {
            files = Array.prototype.slice.call(input.files || []);
            render();
        });
    }

    wireBrochureDialog();
    wireUploadForm();
})();
