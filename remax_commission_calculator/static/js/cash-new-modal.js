(function () {
    var dialog = document.getElementById("cash-new-dialog");
    if (!dialog || dialog.dataset.cashNewReady === "1") return;
    dialog.dataset.cashNewReady = "1";

    var form = dialog.querySelector("[data-cash-new-form]");
    var typeField = dialog.querySelector("[data-cash-new-type]");
    var amountField = dialog.querySelector("[data-cash-new-amount]");
    var currencyField = dialog.querySelector("[data-cash-new-currency]");
    var dateField = dialog.querySelector("[data-cash-new-date]");
    var categoryField = dialog.querySelector("[data-cash-new-category]");
    var descriptionField = dialog.querySelector("[data-cash-new-description]");
    var accountField = dialog.querySelector("[data-cash-new-account]");
    var methodField = dialog.querySelector("[data-cash-new-method]");
    var submitButton = dialog.querySelector("[data-cash-new-submit]");
    var prefix = dialog.querySelector("[data-cash-new-prefix]");
    var originSection = dialog.querySelector("[data-cash-origin]");
    var originOperation = dialog.querySelector("[data-cash-origin-operation]");
    var originSide = dialog.querySelector("[data-cash-origin-side]");
    var originStatus = dialog.querySelector("[data-cash-origin-status]");
    var originError = dialog.querySelector("[data-cash-origin-error]");
    var summaryNodes = {};
    var lastTrigger = null;
    var scrollLocked = false;
    var originCatalog = null;
    var originLoading = false;
    var originBlocked = false;
    var suggestedAmount = "";
    var suggestedDescription = "";

    dialog.querySelectorAll("[data-cash-new-summary]").forEach(function (node) {
        summaryNodes[node.getAttribute("data-cash-new-summary")] = node;
    });

    function pad(value) {
        return value < 10 ? "0" + value : String(value);
    }

    function todayIso() {
        var now = new Date();
        return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
    }

    function parseAmount(raw) {
        if (raw === null || raw === undefined) return NaN;
        var normalized = String(raw).replace(/\s/g, "").replace(/\$/g, "");
        if (!normalized) return NaN;
        if (normalized.indexOf(",") >= 0 && normalized.indexOf(".") >= 0) {
            normalized = normalized.replace(/\./g, "").replace(",", ".");
        } else if (normalized.indexOf(",") >= 0) {
            normalized = normalized.replace(",", ".");
        }
        return parseFloat(normalized);
    }

    function formatAmount(raw, currency) {
        var value = parseAmount(raw);
        if (!isFinite(value)) return "—";
        var formatted = value.toLocaleString("es-AR", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return (currency ? currency + " " : "") + formatted;
    }

    function selectedText(select) {
        if (!select || select.selectedIndex < 0) return "—";
        var option = select.options[select.selectedIndex];
        if (!option || !option.value) return option ? option.text : "—";
        return option.text;
    }

    function syncCategories() {
        var kind = typeField.value;
        categoryField.querySelectorAll("option[data-cash-new-kind]").forEach(function (option) {
            var match = option.getAttribute("data-cash-new-kind") === kind;
            option.hidden = !match;
            option.disabled = !match;
        });
        categoryField.querySelectorAll("optgroup").forEach(function (group) {
            var visible = group.querySelector("option:not([disabled])");
            group.hidden = !visible;
        });
        var current = categoryField.options[categoryField.selectedIndex];
        if (current && current.disabled) {
            categoryField.value = "";
        }
    }

    function syncAccounts() {
        var currency = currencyField.value;
        accountField.querySelectorAll("option[data-currency]").forEach(function (option) {
            var match = !currency || option.getAttribute("data-currency") === currency;
            option.hidden = !match;
            option.disabled = !match;
        });
        var current = accountField.options[accountField.selectedIndex];
        if (current && current.disabled) {
            accountField.value = "";
        }
    }

    function syncSummary() {
        if (prefix) prefix.textContent = currencyField.value || "ARS";
        if (summaryNodes.type) summaryNodes.type.textContent = selectedText(typeField);
        if (summaryNodes.amount) {
            summaryNodes.amount.textContent = formatAmount(amountField.value, currencyField.value);
        }
        if (summaryNodes.account) summaryNodes.account.textContent = selectedText(accountField);
        if (summaryNodes.method) summaryNodes.method.textContent = selectedText(methodField);
        if (summaryNodes.category) {
            summaryNodes.category.textContent = categoryField.value ? selectedText(categoryField) : "—";
        }
        refreshOriginGuard();
        var amount = parseAmount(amountField.value);
        var originReady = true;
        if (originActive() && originOperation && originOperation.value) {
            originReady = Boolean(originSide && originSide.value) && !originBlocked;
        }
        var ready = Boolean(
            typeField.value
            && currencyField.value
            && dateField.value
            && categoryField.value
            && descriptionField.value.trim()
            && methodField.value
            && isFinite(amount)
            && amount > 0
            && originReady
        );
        submitButton.disabled = !ready;
    }

    function originActive() {
        if (!originSection || !typeField || !categoryField) return false;
        var categories = (originSection.getAttribute("data-cash-origin-categories") || "").split(",");
        return typeField.value === "income" && categories.indexOf(categoryField.value) >= 0;
    }

    function formatInputAmount(value) {
        var number = Number(value);
        if (!isFinite(number)) return "";
        return number.toLocaleString("es-AR", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    function selectedOperation() {
        if (!originCatalog || !originOperation || !originOperation.value) return null;
        var id = String(originOperation.value);
        for (var i = 0; i < originCatalog.length; i += 1) {
            if (String(originCatalog[i].id) === id) return originCatalog[i];
        }
        return null;
    }

    function selectedSide(operation) {
        if (!operation || !originSide || !originSide.value) return null;
        for (var i = 0; i < operation.sides.length; i += 1) {
            if (operation.sides[i].role === originSide.value) return operation.sides[i];
        }
        return null;
    }

    function fillOperations() {
        if (!originOperation || !originCatalog) return;
        var current = originOperation.value;
        if (!current && originOperation.hasAttribute("data-selected")) {
            current = originOperation.getAttribute("data-selected") || "";
            originOperation.removeAttribute("data-selected");
        }
        var emptyLabel = originOperation.options[0] ? originOperation.options[0].text : "";
        originOperation.innerHTML = "";
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = emptyLabel;
        originOperation.appendChild(empty);
        originCatalog.forEach(function (operation) {
            var option = document.createElement("option");
            option.value = String(operation.id);
            option.textContent = operation.label;
            originOperation.appendChild(option);
        });
        originOperation.value = current;
        if (originOperation.value !== current) originOperation.value = "";
    }

    function renderSides() {
        if (!originSide) return;
        var operation = selectedOperation();
        var current = originSide.value;
        if (!current && originSide.hasAttribute("data-selected")) {
            current = originSide.getAttribute("data-selected") || "";
            originSide.removeAttribute("data-selected");
        }
        var emptyLabel = originSide.options[0] ? originSide.options[0].text : "";
        originSide.innerHTML = "";
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = emptyLabel;
        originSide.appendChild(empty);
        if (operation) {
            var collectedTemplate = originSection.getAttribute("data-label-side-collected") || "{side} — Cobrada";
            operation.sides.forEach(function (side) {
                var option = document.createElement("option");
                var baseLabel = side.label || side.role;
                option.value = side.role;
                option.textContent = sideIsSettled(side)
                    ? collectedTemplate.replace("{side}", baseLabel)
                    : baseLabel;
                option.disabled = sideIsSettled(side);
                originSide.appendChild(option);
            });
            originSide.disabled = !originActive();
        } else {
            originSide.disabled = true;
        }
        originSide.value = current;
        if (originSide.value !== current) originSide.value = "";
        var selectedOption = originSide.options[originSide.selectedIndex];
        if (selectedOption && selectedOption.disabled) originSide.value = "";
    }

    function sideIsSettled(side) {
        return !side || Number(side.remaining) <= 0.009 || side.selectable === false;
    }

    function operationFullySettled(operation) {
        return Boolean(
            operation
            && operation.sides
            && operation.sides.length
            && operation.sides.every(sideIsSettled)
        );
    }

    function applySuggestion() {
        var operation = selectedOperation();
        var side = selectedSide(operation);
        if (!operation || !side || sideIsSettled(side)) return;
        var amountText = formatInputAmount(side.remaining);
        var template = originSection.getAttribute("data-description-template") || "";
        var sideLabel = side.label || "";
        var descriptionText = template
            .replace("{code}", operation.display_id)
            .replace("{side}", sideLabel);
        var currentAmount = amountField.value.trim();
        if (!currentAmount || currentAmount === suggestedAmount) {
            amountField.value = amountText;
            suggestedAmount = amountText;
        }
        var currentDescription = descriptionField.value.trim();
        if (!currentDescription || currentDescription === suggestedDescription) {
            descriptionField.value = descriptionText;
            suggestedDescription = descriptionText;
        }
        if (operation.currency && currencyField.value !== operation.currency) {
            currencyField.value = operation.currency;
            syncAccounts();
        }
    }

    function clearOriginError() {
        if (!originError) return;
        originError.hidden = true;
        originError.textContent = "";
    }

    function refreshOriginGuard() {
        var operation = selectedOperation();
        var side = selectedSide(operation);
        if (!originActive() || !operation) {
            originBlocked = false;
            clearOriginError();
            return;
        }
        if (operationFullySettled(operation)) {
            originBlocked = true;
            clearOriginError();
            return;
        }
        if (!side || sideIsSettled(side)) {
            originBlocked = Boolean(side && sideIsSettled(side));
            clearOriginError();
            return;
        }
        var remaining = Number(side.remaining);
        var amount = parseAmount(amountField.value);
        var currenciesMatch = !operation.currency || currencyField.value === operation.currency;
        var overPending = currenciesMatch
            && isFinite(remaining)
            && isFinite(amount)
            && amount > remaining + 0.009;
        originBlocked = (isFinite(remaining) && remaining <= 0.009) || overPending;
        if (!originError) return;
        if (!overPending) {
            clearOriginError();
            return;
        }
        var template = originSection.getAttribute("data-label-exceeds") || "";
        originError.hidden = false;
        originError.textContent = template.replace(
            "{amount}",
            formatAmount(remaining, operation.currency)
        );
    }

    function updateOriginStatus() {
        if (!originStatus) return;
        var operation = selectedOperation();
        if (!operation || !operation.sides || !operation.sides.length) {
            originStatus.hidden = true;
            originStatus.textContent = "";
            refreshOriginGuard();
            return;
        }
        var lines = [];
        if (operationFullySettled(operation)) {
            lines.push(originSection.getAttribute("data-label-operation-settled") || "");
        }
        operation.sides.forEach(function (side) {
            var parts = [
                side.label || side.role,
                originSection.getAttribute("data-label-expected") + " " + formatAmount(side.expected, operation.currency),
                originSection.getAttribute("data-label-collected") + " " + formatAmount(side.collected, operation.currency),
                originSection.getAttribute("data-label-remaining") + " " + formatAmount(side.remaining, operation.currency)
            ];
            if (side.count > 1) {
                parts.push(
                    (originSection.getAttribute("data-label-count") || "").replace("{count}", String(side.count))
                );
            }
            if (sideIsSettled(side)) {
                parts.push(originSection.getAttribute("data-label-settled") || "");
            }
            lines.push(parts.filter(Boolean).join(" · "));
        });
        originStatus.hidden = false;
        originStatus.textContent = lines.filter(Boolean).join("\n");
        refreshOriginGuard();
    }

    function ensureOriginCatalog() {
        if (originCatalog || originLoading || !originSection) return;
        originLoading = true;
        var url = originSection.getAttribute("data-cash-origin-url");
        fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
            .then(function (response) { return response.json(); })
            .then(function (payload) {
                originCatalog = payload.operations || [];
                var buyerLabel = originSection.getAttribute("data-label-buyer") || "";
                var sellerLabel = originSection.getAttribute("data-label-seller") || "";
                originCatalog.forEach(function (operation) {
                    (operation.sides || []).forEach(function (side) {
                        side.label = side.role === "seller" ? sellerLabel : buyerLabel;
                    });
                });
                fillOperations();
                renderSides();
                if (originSide && originSide.value) applySuggestion();
                updateOriginStatus();
                syncSummary();
            })
            .catch(function () {
                originCatalog = [];
            })
            .then(function () {
                originLoading = false;
            });
    }

    function syncOrigin() {
        if (!originSection) return;
        var active = originActive();
        originSection.hidden = !active;
        if (originOperation) originOperation.disabled = !active;
        if (!active) {
            originBlocked = false;
            if (originOperation) originOperation.value = "";
            if (originSide) {
                originSide.value = "";
                originSide.disabled = true;
            }
            if (originStatus) {
                originStatus.hidden = true;
                originStatus.textContent = "";
            }
            clearOriginError();
            return;
        }
        ensureOriginCatalog();
        renderSides();
        updateOriginStatus();
    }

    function lockScroll() {
        if (scrollLocked) return;
        scrollLocked = true;
        document.body.style.overflow = "hidden";
        document.documentElement.style.overflow = "hidden";
    }

    function unlockScroll() {
        scrollLocked = false;
        document.body.style.overflow = "";
        document.documentElement.style.overflow = "";
    }

    function openDialog(trigger) {
        lastTrigger = trigger || null;
        if (dateField && !dateField.value) dateField.value = todayIso();
        syncCategories();
        syncAccounts();
        syncOrigin();
        syncSummary();
        if (!dialog.open) {
            lockScroll();
            dialog.showModal();
        }
        if (typeField) typeField.focus();
    }

    function closeDialog() {
        if (dialog.open) dialog.close();
    }

    dialog.querySelectorAll("[data-cash-new-close]").forEach(function (button) {
        button.addEventListener("click", closeDialog);
    });

    dialog.addEventListener("close", function () {
        unlockScroll();
        if (form) form.reset();
        suggestedAmount = "";
        suggestedDescription = "";
        if (dateField) dateField.value = todayIso();
        syncCategories();
        syncAccounts();
        syncOrigin();
        syncSummary();
        if (dialog.hasAttribute("data-return-on-close")) {
            window.location.assign(dialog.getAttribute("data-return-url"));
            return;
        }
        if (lastTrigger && typeof lastTrigger.focus === "function") {
            lastTrigger.focus();
        }
    });

    document.querySelectorAll("[data-cash-new-open]").forEach(function (trigger) {
        trigger.addEventListener("click", function (event) {
            event.preventDefault();
            openDialog(trigger);
        });
    });

    [typeField, amountField, currencyField, dateField, categoryField, descriptionField, accountField, methodField].forEach(function (field) {
        if (!field) return;
        field.addEventListener("input", function () {
            if (field === typeField) syncCategories();
            if (field === currencyField) syncAccounts();
            if (field === typeField || field === categoryField) syncOrigin();
            syncSummary();
        });
        field.addEventListener("change", function () {
            if (field === typeField) syncCategories();
            if (field === currencyField) syncAccounts();
            if (field === typeField || field === categoryField) syncOrigin();
            syncSummary();
        });
    });

    if (originOperation) {
        originOperation.addEventListener("change", function () {
            if (originSide) originSide.value = "";
            renderSides();
            applySuggestion();
            updateOriginStatus();
            syncSummary();
        });
    }
    if (originSide) {
        originSide.addEventListener("change", function () {
            applySuggestion();
            updateOriginStatus();
            syncSummary();
        });
    }

    if (!dateField.value) dateField.value = todayIso();
    syncCategories();
    syncAccounts();
    syncOrigin();
    syncSummary();

    if (dialog.hasAttribute("data-open-on-load")) {
        openDialog(null);
    }
}());
