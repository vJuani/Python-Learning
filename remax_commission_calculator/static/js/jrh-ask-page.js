(function () {
    var root = document.querySelector("[data-jrh-studio]");
    if (!root) {
        return;
    }

    var input = root.querySelector("[data-jrh-ask-input]");
    var form = root.querySelector("[data-jrh-interpret]");
    var examples = (root.getAttribute("data-placeholders") || "").split("|").filter(Boolean);
    if (!examples.length && input) {
        examples = [input.getAttribute("placeholder") || ""];
    }

    var index = 0;
    function rotatePlaceholder() {
        if (!input || input.value || document.activeElement === input) {
            return;
        }
        if (!examples.length) {
            return;
        }
        input.setAttribute("placeholder", examples[index % examples.length]);
        index += 1;
    }
    if (examples.length > 1 && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        window.setInterval(rotatePlaceholder, 4200);
    }

    root.querySelectorAll("[data-jrh-example]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (!input) {
                return;
            }
            input.value = button.getAttribute("data-jrh-example") || "";
            input.focus();
        });
    });

    if (form) {
        form.addEventListener("submit", function () {
            root.classList.add("is-thinking");
            document.body.classList.add("is-jrh-thinking");
            var processing = root.querySelector("[data-jrh-processing]");
            if (processing) {
                processing.hidden = false;
            }
        });
    }

    var hashTarget = document.querySelector("#jrh-ask-prompt");
    if (window.location.hash === "#jrh-ask-prompt" && hashTarget) {
        hashTarget.focus();
    }
})();
