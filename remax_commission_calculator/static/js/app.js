document.addEventListener("DOMContentLoaded", function () {
    var themeToggles = document.querySelectorAll(".theme-toggle");
    var root = document.documentElement;
    var systemMq = window.matchMedia("(prefers-color-scheme: dark)");

    function readThemePref() {
        try {
            var stored = localStorage.getItem("cc-theme");
            if (stored === "dark" || stored === "light" || stored === "system") {
                return stored;
            }
        } catch (error) {
            /* ignore */
        }
        return "system";
    }

    function resolveTheme(pref) {
        if (pref === "system") {
            return systemMq.matches ? "dark" : "light";
        }
        return pref === "dark" ? "dark" : "light";
    }

    function applyTheme(pref) {
        if (pref !== "dark" && pref !== "light" && pref !== "system") {
            pref = "system";
        }
        var resolved = resolveTheme(pref);
        root.setAttribute("data-theme", resolved);
        root.setAttribute("data-theme-pref", pref);

        themeToggles.forEach(function (themeToggle) {
            var label = resolved === "dark"
                ? themeToggle.getAttribute("data-label-light")
                : themeToggle.getAttribute("data-label-dark");

            themeToggle.setAttribute("aria-label", label || "");
            themeToggle.setAttribute("title", label || "");
        });

        try {
            localStorage.setItem("cc-theme", pref);
        } catch (error) {
            /* ignore */
        }
        document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) {
            var media = meta.getAttribute("media") || "";
            if (!media) {
                meta.setAttribute("content", resolved === "dark" ? "#080A0F" : "#0E4F38");
            }
        });
        syncAppearanceLabels(pref, resolved);
    }

    window.__jrhApplyTheme = applyTheme;

    function syncAppearanceLabels(pref, resolved) {
        resolved = resolved || resolveTheme(pref);
        document.querySelectorAll(".m-appearance-value").forEach(function (node) {
            var key = pref === "system" ? "system" : resolved;
            node.textContent = node.getAttribute("data-label-" + key) || key;
        });
        document.querySelectorAll("[data-set-theme]").forEach(function (btn) {
            btn.classList.toggle("is-active", btn.getAttribute("data-set-theme") === pref);
        });
    }

    if (themeToggles.length) {
        themeToggles.forEach(function (themeToggle) {
            themeToggle.addEventListener("click", function () {
                var isDark = root.getAttribute("data-theme") === "dark";
                applyTheme(isDark ? "light" : "dark");
            });
        });
    }

    applyTheme(readThemePref());

    if (typeof systemMq.addEventListener === "function") {
        systemMq.addEventListener("change", function () {
            if (readThemePref() === "system") {
                applyTheme("system");
            }
        });
    } else if (typeof systemMq.addListener === "function") {
        systemMq.addListener(function () {
            if (readThemePref() === "system") {
                applyTheme("system");
            }
        });
    }

    var railToggles = document.querySelectorAll(".rail-toggle");
    var desktopRailMq = window.matchMedia("(min-width: 1025px)");

    function applyRailCollapsed(collapsed) {
        if (collapsed) {
            root.setAttribute("data-rail", "collapsed");
        } else {
            root.removeAttribute("data-rail");
        }

        railToggles.forEach(function (railToggle) {
            var label = collapsed
                ? railToggle.getAttribute("data-label-expand")
                : railToggle.getAttribute("data-label-collapse");
            var textNode = railToggle.querySelector(".rail-toggle-text");

            railToggle.setAttribute(
                "aria-expanded",
                collapsed ? "false" : "true"
            );
            railToggle.setAttribute("aria-label", label || "");
            railToggle.setAttribute("title", label || "");

            if (textNode) {
                textNode.textContent = label || "";
            }

            if (railToggle.classList.contains("rail-toggle-header")) {
                railToggle.hidden = !(
                    desktopRailMq.matches && collapsed
                );
            }
        });

        try {
            localStorage.setItem(
                "cc-rail",
                collapsed ? "collapsed" : "expanded"
            );
        } catch (error) {
            /* ignore */
        }
    }

    if (railToggles.length) {
        railToggles.forEach(function (railToggle) {
            railToggle.addEventListener("click", function () {
                var isCollapsed = root.getAttribute("data-rail") === "collapsed";
                applyRailCollapsed(!isCollapsed);
            });
        });

        applyRailCollapsed(
            root.getAttribute("data-rail") === "collapsed"
        );

        if (typeof desktopRailMq.addEventListener === "function") {
            desktopRailMq.addEventListener("change", function () {
                applyRailCollapsed(
                    root.getAttribute("data-rail") === "collapsed"
                );
            });
        } else if (typeof desktopRailMq.addListener === "function") {
            desktopRailMq.addListener(function () {
                applyRailCollapsed(
                    root.getAttribute("data-rail") === "collapsed"
                );
            });
        }
    }

    var toggle = document.querySelector(".nav-toggle");
    var nav = document.querySelector(".main-nav");
    var shell = document.querySelector(".app-shell");
    var sidebar = document.getElementById("app-sidebar");
    var filtersToggle = document.getElementById("filters-toggle");
    var mobileMq = window.matchMedia("(max-width: 768px)");
    var moreButtons = document.querySelectorAll("[data-mobile-nav-more]");
    var morePanel = document.getElementById("mobile-more");
    var sheetBackdrop = document.querySelector("[data-m-sheet-backdrop]");
    var dynamicSheet = document.getElementById("m-sheet-dynamic");
    var dynamicBody = dynamicSheet && dynamicSheet.querySelector("[data-sheet-body]");
    var dynamicTitle = dynamicSheet && dynamicSheet.querySelector("[data-sheet-title]");
    var portaledPanel = null;
    var portaledHome = null;
    var scrollLockY = 0;

    function isMobileNav() {
        return mobileMq.matches;
    }

    function lockPageScroll() {
        if (document.body.classList.contains("m-scroll-locked")) {
            return;
        }
        scrollLockY = window.scrollY || window.pageYOffset || 0;
        document.body.style.top = "-" + scrollLockY + "px";
        document.body.classList.add("m-scroll-locked");
    }

    function unlockPageScroll() {
        if (document.body.classList.contains("m-more-open") || document.body.classList.contains("m-sheet-open")) {
            return;
        }
        if (!document.body.classList.contains("m-scroll-locked")) {
            return;
        }
        document.body.classList.remove("m-scroll-locked");
        document.body.style.top = "";
        window.scrollTo(0, scrollLockY);
    }

    function syncMoreActive(isOpen) {
        moreButtons.forEach(function (btn) {
            btn.classList.toggle("active", isOpen);
            btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
        document.documentElement.classList.toggle("m-nav-open", isOpen);
        document.body.classList.toggle("m-nav-open", isOpen);
        document.documentElement.classList.toggle("m-more-open", isOpen);
        document.body.classList.toggle("m-more-open", isOpen);
        if (isOpen) {
            lockPageScroll();
        } else {
            unlockPageScroll();
        }
    }

    function restorePortaledPanel() {
        if (portaledPanel && portaledHome) {
            portaledHome.appendChild(portaledPanel);
        }
        portaledPanel = null;
        portaledHome = null;
    }

    function openPortaledPanel(panel, title) {
        if (!isMobileNav() || !dynamicSheet || !dynamicBody || !panel) {
            return false;
        }
        var form = panel.closest("form");
        restorePortaledPanel();
        portaledHome = panel.parentNode;
        portaledPanel = panel;
        if (form) {
            if (!form.id) {
                form.id = "m-sheet-bound-form";
            }
            panel.querySelectorAll("input, select, textarea, button").forEach(function (el) {
                el.setAttribute("form", form.id);
            });
        }
        if (dynamicTitle) {
            dynamicTitle.textContent = title || "";
        }
        dynamicBody.appendChild(panel);
        openNamedSheet("dynamic");
        var sheetPanel = dynamicSheet.querySelector(".m-sheet__panel");
        if (sheetPanel) {
            sheetPanel.scrollTop = 0;
        }
        return true;
    }

    function closeAllSheets() {
        restorePortaledPanel();
        document.querySelectorAll(".m-sheet").forEach(function (sheet) {
            sheet.hidden = true;
            sheet.classList.remove("is-open");
        });
        document.querySelectorAll("[data-filter-sheet], .properties-toolbar__more").forEach(function (details) {
            details.open = false;
            details.classList.remove("is-sheet-open");
        });
        document.documentElement.classList.remove("m-sheet-open");
        document.body.classList.remove("m-sheet-open");
        if (sheetBackdrop) {
            sheetBackdrop.hidden = true;
        }
        unlockPageScroll();
    }

    function openNamedSheet(name) {
        var sheet = document.getElementById("m-sheet-" + name);
        if (!sheet) {
            return;
        }
        document.querySelectorAll(".m-sheet").forEach(function (node) {
            if (node === sheet) {
                return;
            }
            node.hidden = true;
            node.classList.remove("is-open");
        });
        if (name !== "dynamic") {
            restorePortaledPanel();
        }
        sheet.hidden = false;
        sheet.classList.add("is-open");
        document.documentElement.classList.add("m-sheet-open");
        document.body.classList.add("m-sheet-open");
        if (sheetBackdrop) {
            sheetBackdrop.hidden = false;
        }
        lockPageScroll();
    }

    function setPropertyFiltersOpen(isOpen) {
        var details = document.querySelector("[data-filter-sheet], .properties-toolbar__more");
        if (!isOpen) {
            closeAllSheets();
            return;
        }
        var panel = details && details.querySelector("[data-sheet-panel], .properties-toolbar__more-panel");
        var titleNode = details && details.querySelector("summary");
        var title = titleNode ? titleNode.textContent.trim() : "";
        if (panel && openPortaledPanel(panel, title)) {
            if (details) {
                details.open = false;
            }
            return;
        }
        document.documentElement.classList.add("m-sheet-open");
        document.body.classList.add("m-sheet-open");
        if (details) {
            details.open = true;
            details.classList.toggle("is-sheet-open", true);
        }
        if (sheetBackdrop) {
            sheetBackdrop.hidden = false;
        }
        lockPageScroll();
    }

    function setMoreOpen(isOpen) {
        if (isMobileNav() && morePanel) {
            morePanel.hidden = !isOpen;
            morePanel.classList.toggle("is-open", isOpen);
            syncMoreActive(isOpen);
            if (isOpen) {
                closeAllSheets();
                if (nav) {
                    nav.classList.remove("is-open");
                }
            }
            return;
        }
        if (morePanel) {
            morePanel.hidden = true;
            morePanel.classList.remove("is-open");
        }
        setNavToggleOpen(isOpen);
    }

    function setNavToggleOpen(isOpen) {
        if (!toggle || !nav) {
            return;
        }

        nav.classList.toggle("is-open", isOpen);
        toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");

        var label = isOpen
            ? (toggle.getAttribute("data-label-close") || "Close")
            : (toggle.getAttribute("data-label-menu") || "Menu");
        var labelNode = toggle.querySelector(".nav-toggle-label");

        toggle.setAttribute("aria-label", label);
        toggle.setAttribute("title", label);
        toggle.classList.toggle("is-open", isOpen);

        if (labelNode) {
            labelNode.textContent = label;
        }
    }

    function closeNav() {
        if (isMobileNav()) {
            setMoreOpen(false);
            closeAllSheets();
            if (nav) {
                nav.classList.remove("is-open");
            }
            return;
        }
        setNavToggleOpen(false);
    }

    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            var isOpen = !nav.classList.contains("is-open");
            setNavToggleOpen(isOpen);

            if (isOpen) {
                setFiltersOpen(false);
            }
        });
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

        var hasInlineMobileFilters = !!document.querySelector(
            ".mobile-inline-filters"
        );
        var hasFilters = sidebar.children.length > 0
            && !sidebar.classList.contains("is-empty")
            && !sidebar.hasAttribute("hidden");
        var isMobile = mobileMq.matches;

        filtersToggle.hidden = !(
            hasFilters
            && isMobile
            && !hasInlineMobileFilters
        );

        if (!hasFilters || !isMobile || hasInlineMobileFilters) {
            setFiltersOpen(false);
        }
    }

    syncFiltersToggle();

    function syncMobileShell() {
        syncFiltersToggle();
        if (!isMobileNav()) {
            if (morePanel) {
                morePanel.hidden = true;
                morePanel.classList.remove("is-open");
            }
            document.documentElement.classList.remove("m-more-open");
            document.body.classList.remove("m-more-open");
            setPropertyFiltersOpen(false);
            moreButtons.forEach(function (btn) {
                btn.classList.remove("active");
                btn.setAttribute("aria-expanded", "false");
            });
        }
    }

    if (typeof mobileMq.addEventListener === "function") {
        mobileMq.addEventListener("change", syncMobileShell);
    } else if (typeof mobileMq.addListener === "function") {
        mobileMq.addListener(syncMobileShell);
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
        closeAllSheets();
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

    document.querySelectorAll("[data-mobile-nav-close]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            closeNav();
        });
    });

    moreButtons.forEach(function (mobileNavMore) {
        mobileNavMore.addEventListener("click", function () {
            var isOpen = morePanel
                ? morePanel.classList.contains("is-open")
                : !!(nav && nav.classList.contains("is-open"));
            setMoreOpen(!isOpen);
        });
    });

    document.querySelectorAll("[data-open-property-filters]").forEach(function (btn) {
        btn.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            document.querySelectorAll(".m-sheet").forEach(function (sheet) {
                sheet.hidden = true;
                sheet.classList.remove("is-open");
            });
            setMoreOpen(false);
            setPropertyFiltersOpen(true);
        });
    });

    document.querySelectorAll("[data-close-property-filters]").forEach(function (btn) {
        btn.addEventListener("click", function (event) {
            event.preventDefault();
            setPropertyFiltersOpen(false);
        });
    });

    document.querySelectorAll("[data-open-sheet]").forEach(function (btn) {
        btn.addEventListener("click", function (event) {
            event.preventDefault();
            openNamedSheet(btn.getAttribute("data-open-sheet"));
        });
    });

    document.querySelectorAll("[data-close-sheet]").forEach(function (btn) {
        btn.addEventListener("click", function (event) {
            event.preventDefault();
            closeAllSheets();
        });
    });

    document.querySelectorAll("[data-set-theme]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var pref = btn.getAttribute("data-set-theme");
            if (pref !== "dark" && pref !== "light" && pref !== "system") {
                pref = "light";
            }
            applyTheme(pref);
        });
    });

    if (sheetBackdrop) {
        sheetBackdrop.addEventListener("click", function () {
            closeAllSheets();
        });
    }

    document.querySelectorAll(".settings-mobile-row").forEach(function (row) {
        row.addEventListener("click", function (event) {
            event.preventDefault();
            document.body.classList.add("settings-mobile-edit");
            var formSection = document.getElementById("settings-form");
            if (formSection) {
                formSection.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    });

    var settingsMobileBack = document.querySelector("[data-settings-mobile-back]");
    if (settingsMobileBack) {
        settingsMobileBack.addEventListener("click", function () {
            document.body.classList.remove("settings-mobile-edit");
            window.scrollTo({ top: 0, behavior: "smooth" });
        });
    }

    document.addEventListener("error", function (event) {
        var target = event.target;
        if (!target || target.tagName !== "IMG" || !target.hasAttribute("data-media-fallback")) {
            return;
        }
        if (window.__jrhMediaFallback) {
            window.__jrhMediaFallback(target);
        }
    }, true);

    document.querySelectorAll("img[data-media-fallback]").forEach(function (img) {
        if (img.complete && img.naturalWidth === 0 && window.__jrhMediaFallback) {
            window.__jrhMediaFallback(img);
        }
    });

    document.querySelectorAll("[data-property-gallery]").forEach(function (gallery) {
        gallery.querySelectorAll("[data-media-url]").forEach(function (button) {
            if (button.tagName !== "BUTTON") {
                return;
            }
            button.addEventListener("click", function () {
                var hero = gallery.querySelector(".property-gallery__hero");
                var nextUrl = button.getAttribute("data-media-url") || "";
                if (!nextUrl) {
                    return;
                }
                if (!hero) {
                    var wrap = gallery.querySelector(".property-gallery__hero-wrap");
                    if (!wrap) {
                        return;
                    }
                    hero = document.createElement("img");
                    hero.className = "property-gallery__hero";
                    hero.alt = "";
                    hero.setAttribute("referrerpolicy", "no-referrer");
                    hero.setAttribute("data-media-fallback", "");
                    hero.setAttribute("decoding", "async");
                    hero.onerror = function () {
                        if (window.__jrhMediaFallback) {
                            window.__jrhMediaFallback(hero);
                        }
                    };
                    wrap.innerHTML = "";
                    wrap.appendChild(hero);
                }
                hero.setAttribute("data-media-fallback-applied", "");
                hero.src = nextUrl;
                hero.setAttribute("data-media-url", nextUrl);
                gallery.querySelectorAll("button[data-media-url]").forEach(function (item) {
                    item.classList.toggle("is-active", item === button);
                });
            });
        });
    });

    var operationsMobileFiltersOpen = document.querySelector("[data-operations-mobile-filters-open]");
    var operationsMobileAdvanced = document.querySelector(".operations-mobile-advanced");
    if (operationsMobileFiltersOpen && operationsMobileAdvanced) {
        operationsMobileFiltersOpen.addEventListener("click", function (event) {
            event.preventDefault();
            var panel = operationsMobileAdvanced.querySelector("[data-sheet-panel]");
            var title = operationsMobileFiltersOpen.textContent.trim();
            if (!openPortaledPanel(panel, title) && operationsMobileAdvanced) {
                operationsMobileAdvanced.setAttribute("open", "open");
            }
        });
        operationsMobileAdvanced.addEventListener("click", function (event) {
            if (event.target === operationsMobileAdvanced) {
                operationsMobileAdvanced.removeAttribute("open");
            }
        });
    }

    document.querySelectorAll(
        "[data-filter-sheet], .jrh-chip-more, .contacts-filters-more, .operations-mobile-advanced, .agenda-fab, .contacts-hero__add"
    ).forEach(function (details) {
        var summary = details.querySelector(":scope > summary");
        var panel = details.querySelector("[data-sheet-panel]");
        if (!summary || !panel) {
            return;
        }
        summary.addEventListener("click", function (event) {
            if (!isMobileNav()) {
                return;
            }
            event.preventDefault();
            details.open = false;
            openPortaledPanel(panel, summary.textContent.trim());
        });
    });

    function syncMobileKeyboard() {
        if (!window.visualViewport || !isMobileNav()) {
            document.documentElement.style.setProperty("--m-keyboard", "0px");
            document.body.classList.remove("m-keyboard-open");
            document.documentElement.classList.remove("m-keyboard-open");
            return;
        }
        var inset = Math.max(
            0,
            window.innerHeight - window.visualViewport.height - window.visualViewport.offsetTop
        );
        document.documentElement.style.setProperty("--m-keyboard", inset + "px");
        document.body.classList.toggle("m-keyboard-open", inset > 80);
        document.documentElement.classList.toggle("m-keyboard-open", inset > 80);
    }

    syncMobileKeyboard();
    if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", syncMobileKeyboard);
        window.visualViewport.addEventListener("scroll", syncMobileKeyboard);
    }
    window.addEventListener("resize", syncMobileKeyboard);
});
