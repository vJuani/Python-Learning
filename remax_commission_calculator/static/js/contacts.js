(function () {
    function addChip(root, name, value) {
        var text = (value || "").trim();
        if (!text) {
            return;
        }
        var row = root.querySelector(".contact-chip-row");
        if (!row) {
            return;
        }
        var label = document.createElement("label");
        label.className = "contact-chip-edit";
        label.appendChild(document.createTextNode(text + " "));
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = name;
        hidden.value = text;
        var button = document.createElement("button");
        button.type = "button";
        button.setAttribute("data-chip-remove", "1");
        button.textContent = "×";
        label.appendChild(hidden);
        label.appendChild(button);
        row.appendChild(label);
    }

    document.querySelectorAll("[data-contact-chips]").forEach(function (root) {
        var input = root.querySelector("[data-chip-input]");
        var add = root.querySelector("[data-chip-add]");
        var name = add && add.getAttribute("data-chip-name");

        root.addEventListener("click", function (event) {
            if (event.target.closest("[data-chip-remove]")) {
                event.preventDefault();
                var chip = event.target.closest(".contact-chip-edit");
                if (chip) {
                    chip.remove();
                }
            }
        });

        if (add && input && name) {
            add.addEventListener("click", function () {
                addChip(root, name, input.value);
                input.value = "";
                input.focus();
            });
            input.addEventListener("keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    addChip(root, name, input.value);
                    input.value = "";
                }
            });
        }
    });
})();
