document.addEventListener("DOMContentLoaded", function () {
    var themeToggle = document.getElementById("theme-toggle");
    var root = document.documentElement;

    function applyTheme(theme) {
        if (theme === "dark") {
            root.setAttribute("data-theme", "dark");
        } else {
            root.removeAttribute("data-theme");
        }

        if (themeToggle) {
            var label = theme === "dark"
                ? themeToggle.getAttribute("data-label-light")
                : themeToggle.getAttribute("data-label-dark");

            themeToggle.setAttribute("aria-label", label || "");
            themeToggle.setAttribute("title", label || "");
        }

        try {
            localStorage.setItem("cc-theme", theme);
        } catch (error) {
            /* ignore */
        }
    }

    if (themeToggle) {
        themeToggle.addEventListener("click", function () {
            var isDark = root.getAttribute("data-theme") === "dark";
            applyTheme(isDark ? "light" : "dark");
        });

        var currentTheme = root.getAttribute("data-theme") === "dark"
            ? "dark"
            : "light";
        applyTheme(currentTheme);
    }

    var toggle = document.querySelector(".nav-toggle");
    var nav = document.querySelector(".main-nav");

    if (toggle && nav) {
        var menuLabel = toggle.getAttribute("data-label-menu") || "Menu";
        var closeLabel = toggle.getAttribute("data-label-close") || "Close";

        toggle.addEventListener("click", function () {
            var isOpen = nav.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
            toggle.textContent = isOpen ? closeLabel : menuLabel;
        });
    }

    var currencySelect = document.getElementById("currency");
    var exchangeGroup = document.getElementById("exchange-rate-group");
    var exchangeInput = document.getElementById("exchange_rate");

    function syncExchangeRateField() {
        if (!currencySelect || !exchangeGroup || !exchangeInput) {
            return;
        }

        var isArs = currencySelect.value === "ARS";

        if (isArs) {
            exchangeGroup.hidden = false;
            exchangeInput.required = true;
        } else {
            exchangeGroup.hidden = true;
            exchangeInput.required = false;
            exchangeInput.value = "";
        }
    }

    if (currencySelect) {
        currencySelect.addEventListener("change", syncExchangeRateField);
        syncExchangeRateField();
    }

    var roleSelect = document.getElementById("role");
    var linkedAgentGroup = document.getElementById("linked-agent-group");
    var linkedAgentSelect = document.getElementById("agent_id");

    function syncLinkedAgentField() {
        if (!roleSelect || !linkedAgentGroup || !linkedAgentSelect) {
            return;
        }

        var isAgentRole = roleSelect.value === "agent";

        if (isAgentRole) {
            linkedAgentGroup.hidden = false;
        } else {
            linkedAgentGroup.hidden = true;
            linkedAgentSelect.value = "";
        }
    }

    if (roleSelect) {
        roleSelect.addEventListener("change", syncLinkedAgentField);
        syncLinkedAgentField();
    }

    document.querySelectorAll(".password-toggle").forEach(function (button) {
        button.addEventListener("click", function () {
            var field = button.closest(".password-field");

            if (!field) {
                return;
            }

            var input = field.querySelector(".password-input");

            if (!input) {
                return;
            }

            var isHidden = input.type === "password";

            input.type = isHidden ? "text" : "password";
            button.classList.toggle("is-visible", isHidden);
            button.setAttribute(
                "aria-pressed",
                isHidden ? "true" : "false"
            );
            button.setAttribute(
                "aria-label",
                isHidden
                    ? button.getAttribute("data-label-hide")
                    : button.getAttribute("data-label-show")
            );
        });
    });
});
