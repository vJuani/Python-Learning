document.addEventListener("DOMContentLoaded", function () {
    var themeToggles = document.querySelectorAll(".theme-toggle");
    var root = document.documentElement;

    function applyTheme(theme) {
        if (theme === "dark") {
            root.setAttribute("data-theme", "dark");
        } else {
            root.removeAttribute("data-theme");
        }

        themeToggles.forEach(function (themeToggle) {
            var label = theme === "dark"
                ? themeToggle.getAttribute("data-label-light")
                : themeToggle.getAttribute("data-label-dark");

            themeToggle.setAttribute("aria-label", label || "");
            themeToggle.setAttribute("title", label || "");
        });

        try {
            localStorage.setItem("cc-theme", theme);
        } catch (error) {
            /* ignore */
        }
    }

    if (themeToggles.length) {
        themeToggles.forEach(function (themeToggle) {
            themeToggle.addEventListener("click", function () {
                var isDark = root.getAttribute("data-theme") === "dark";
                applyTheme(isDark ? "light" : "dark");
            });
        });

        var currentTheme = root.getAttribute("data-theme") === "dark"
            ? "dark"
            : "light";
        applyTheme(currentTheme);
    }

    var toggle = document.querySelector(".nav-toggle");
    var nav = document.querySelector(".main-nav");
    var shell = document.querySelector(".app-shell");
    var sidebar = document.getElementById("app-sidebar");
    var filtersToggle = document.getElementById("filters-toggle");
    var filtersChip = document.getElementById("filters-active-chip");
    var mobileMq = window.matchMedia("(max-width: 768px)");

    function closeNav() {
        if (!toggle || !nav) {
            return;
        }

        nav.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.textContent = toggle.getAttribute("data-label-menu") || "Menu";
    }

    function setFiltersOpen(isOpen) {
        if (!shell || !filtersToggle) {
            return;
        }

        shell.classList.toggle("filters-open", isOpen);
        filtersToggle.setAttribute(
            "aria-expanded",
            isOpen ? "true" : "false"
        );
        filtersToggle.textContent = isOpen
            ? (filtersToggle.getAttribute("data-label-close") || "Hide filters")
            : (filtersToggle.getAttribute("data-label-open") || "Filters");
    }

    function syncFiltersToggle() {
        if (!shell || !sidebar || !filtersToggle) {
            return;
        }

        var hasFilters = sidebar.children.length > 0;
        var isMobile = mobileMq.matches;

        filtersToggle.hidden = !(hasFilters && isMobile);

        if (!hasFilters || !isMobile) {
            setFiltersOpen(false);
        }

        if (filtersChip) {
            var countEl = sidebar.querySelector(".sidebar-count");

            if (hasFilters && isMobile && countEl) {
                filtersChip.hidden = false;
                filtersChip.textContent = countEl.textContent.trim();
            } else {
                filtersChip.hidden = true;
                filtersChip.textContent = "";
            }
        }
    }

    if (toggle && nav) {
        var menuLabel = toggle.getAttribute("data-label-menu") || "Menu";
        var closeLabel = toggle.getAttribute("data-label-close") || "Close";

        toggle.addEventListener("click", function () {
            var isOpen = nav.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
            toggle.textContent = isOpen ? closeLabel : menuLabel;

            if (isOpen) {
                setFiltersOpen(false);
            }
        });
    }

    if (filtersToggle && shell) {
        filtersToggle.addEventListener("click", function () {
            var nextOpen = !shell.classList.contains("filters-open");
            setFiltersOpen(nextOpen);

            if (nextOpen) {
                closeNav();
            }
        });
    }

    syncFiltersToggle();

    if (typeof mobileMq.addEventListener === "function") {
        mobileMq.addEventListener("change", syncFiltersToggle);
    } else if (typeof mobileMq.addListener === "function") {
        mobileMq.addListener(syncFiltersToggle);
    }

    document.querySelectorAll("details.nav-dropdown").forEach(function (dropdown) {
        dropdown.addEventListener("toggle", function () {
            if (!dropdown.open) {
                return;
            }

            document.querySelectorAll("details.nav-dropdown[open]").forEach(function (other) {
                if (other !== dropdown) {
                    other.open = false;
                }
            });
        });
    });

    document.addEventListener("click", function (event) {
        var target = event.target;

        if (target.closest && target.closest("details.nav-dropdown")) {
            return;
        }

        document.querySelectorAll("details.nav-dropdown[open]").forEach(function (dropdown) {
            dropdown.open = false;
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") {
            return;
        }

        document.querySelectorAll("details.nav-dropdown[open]").forEach(function (dropdown) {
            dropdown.open = false;
        });
        closeNav();
        setFiltersOpen(false);
    });

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

    var operationAgentSelect = document.getElementById("agent_id");
    var operationPropertySelect = document.getElementById("property_id");
    var operationPropertyHint = document.getElementById("property-agent-hint");

    function syncOperationPropertyOptions() {
        if (!operationPropertySelect) {
            return;
        }

        var selectedAgentId = "";

        if (operationAgentSelect) {
            selectedAgentId = String(operationAgentSelect.value || "");
        }

        var emptyLabel = operationPropertySelect.getAttribute(
            "data-empty-label"
        ) || "";
        var noAgentLabel = operationPropertySelect.getAttribute(
            "data-no-agent-label"
        ) || "";
        var noPropertiesLabel = operationPropertySelect.getAttribute(
            "data-no-properties-label"
        ) || "";
        var previousValue = String(operationPropertySelect.value || "");
        var visibleCount = 0;
        var keepSelection = false;

        Array.prototype.forEach.call(
            operationPropertySelect.options,
            function (option) {
                if (!option.value) {
                    option.hidden = false;
                    option.disabled = false;
                    return;
                }

                var optionAgentId = String(
                    option.getAttribute("data-agent-id") || ""
                );
                var matches = (
                    selectedAgentId !== ""
                    && optionAgentId !== ""
                    && optionAgentId === selectedAgentId
                );

                option.hidden = !matches;
                option.disabled = !matches;

                if (matches) {
                    visibleCount += 1;

                    if (option.value === previousValue) {
                        keepSelection = true;
                    }
                }
            }
        );

        if (!keepSelection) {
            operationPropertySelect.value = "";
        }

        if (operationPropertyHint) {
            if (!selectedAgentId) {
                operationPropertyHint.hidden = false;
                operationPropertyHint.textContent = noAgentLabel;
            } else if (visibleCount === 0) {
                operationPropertyHint.hidden = false;
                operationPropertyHint.textContent = noPropertiesLabel;
            } else {
                operationPropertyHint.hidden = true;
                operationPropertyHint.textContent = "";
            }
        }

        var placeholder = operationPropertySelect.querySelector(
            'option[value=""]'
        );

        if (placeholder) {
            if (!selectedAgentId) {
                placeholder.textContent = noAgentLabel || emptyLabel;
            } else if (visibleCount === 0) {
                placeholder.textContent = noPropertiesLabel || emptyLabel;
            } else {
                placeholder.textContent = emptyLabel;
            }
        }
    }

    if (operationPropertySelect) {
        if (operationAgentSelect && !operationAgentSelect.disabled) {
            operationAgentSelect.addEventListener(
                "change",
                syncOperationPropertyOptions
            );
        }

        syncOperationPropertyOptions();
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
