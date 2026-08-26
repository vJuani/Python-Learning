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

        var hasFilters = sidebar.children.length > 0
            && !sidebar.classList.contains("is-empty")
            && !sidebar.hasAttribute("hidden");
        var isMobile = mobileMq.matches;

        filtersToggle.hidden = !(hasFilters && isMobile);

        if (!hasFilters || !isMobile) {
            setFiltersOpen(false);
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

    function initAutocomplete(form) {
        var input = form.querySelector("[data-autocomplete-input]");
        var list = form.querySelector("[data-autocomplete-list]");
        var suggestUrl = form.getAttribute("data-suggest-url");
        var minChars = parseInt(form.getAttribute("data-min-chars") || "1", 10);
        var debounceMs = parseInt(form.getAttribute("data-debounce-ms") || "250", 10);
        var emptyLabel = form.getAttribute("data-empty-label") || "";
        var debounceTimer = null;
        var activeIndex = -1;
        var items = [];

        if (!input || !list || !suggestUrl) {
            return;
        }

        function closeList() {
            list.hidden = true;
            list.innerHTML = "";
            input.setAttribute("aria-expanded", "false");
            activeIndex = -1;
            items = [];
        }

        function openList() {
            list.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }

        function selectItem(item) {
            input.value = item.name;
            closeList();
            form.submit();
        }

        function renderItems(nextItems) {
            items = nextItems || [];
            list.innerHTML = "";
            activeIndex = -1;

            if (!items.length) {
                var empty = document.createElement("li");
                empty.className = "autocomplete-empty";
                empty.textContent = emptyLabel;
                empty.setAttribute("role", "presentation");
                list.appendChild(empty);
                openList();
                return;
            }

            items.forEach(function (item, index) {
                var option = document.createElement("li");
                option.className = "autocomplete-option";
                option.setAttribute("role", "option");
                option.setAttribute("id", "ac-opt-" + index);
                option.textContent = item.name;
                option.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    selectItem(item);
                });
                list.appendChild(option);
            });

            openList();
        }

        function fetchSuggestions(query) {
            if (query.length < minChars) {
                closeList();
                return;
            }

            var url = suggestUrl + (suggestUrl.indexOf("?") >= 0 ? "&" : "?") + "q=" + encodeURIComponent(query);

            fetch(url, {
                headers: { "Accept": "application/json" },
                credentials: "same-origin"
            }).then(function (response) {
                if (!response.ok) {
                    throw new Error("suggest failed");
                }
                return response.json();
            }).then(function (data) {
                if (input.value.trim() !== query) {
                    return;
                }
                renderItems(Array.isArray(data) ? data : []);
            }).catch(function () {
                closeList();
            });
        }

        input.addEventListener("input", function () {
            var query = input.value.trim();
            window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(function () {
                fetchSuggestions(query);
            }, debounceMs);
        });

        input.addEventListener("keydown", function (event) {
            if (list.hidden) {
                if (event.key === "ArrowDown" && input.value.trim().length >= minChars) {
                    fetchSuggestions(input.value.trim());
                }
                return;
            }

            var options = list.querySelectorAll(".autocomplete-option");

            if (event.key === "Escape") {
                closeList();
                return;
            }

            if (event.key === "ArrowDown") {
                event.preventDefault();
                activeIndex = Math.min(activeIndex + 1, options.length - 1);
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                activeIndex = Math.max(activeIndex - 1, 0);
            } else if (event.key === "Enter" && activeIndex >= 0 && items[activeIndex]) {
                event.preventDefault();
                selectItem(items[activeIndex]);
                return;
            } else {
                return;
            }

            options.forEach(function (option, index) {
                option.classList.toggle("is-active", index === activeIndex);
            });
        });

        document.addEventListener("click", function (event) {
            if (!form.contains(event.target)) {
                closeList();
            }
        });
    }

    document.querySelectorAll("[data-autocomplete]").forEach(initAutocomplete);
});
