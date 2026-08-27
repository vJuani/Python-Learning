(function () {
    var form = document.getElementById("operation-new");
    if (!form) {
        return;
    }

    var propertyIdInput = document.getElementById("property_id");
    var propertySearchInput = document.getElementById("property_search");
    var propertySearchList = document.getElementById("property-search-list");
    var propertySearchHint = document.getElementById("property-search-hint");
    var agentSelect = document.getElementById("agent_id");
    var agentFieldGroup = document.getElementById("agent-field-group");
    var currencySelect = document.getElementById("currency");
    var originalAmountInput = document.getElementById("original_amount");
    var exchangeRateGroup = document.getElementById("exchange-rate-group");
    var exchangeRateInput = document.getElementById("exchange_rate");
    var sellerSideActive = document.getElementById("seller_side_active");
    var buyerSideActive = document.getElementById("buyer_side_active");
    var isReferred = document.getElementById("is_referred");
    var referredSideGroup = document.getElementById("referred-side-group");
    var sellerRateInput = document.getElementById("seller_commission_rate");
    var buyerRateInput = document.getElementById("buyer_commission_rate");
    var sellerVatInput = document.getElementById("seller_vat_amount");
    var buyerVatInput = document.getElementById("buyer_vat_amount");
    var sellerCard = document.getElementById("seller-side-card");
    var buyerCard = document.getElementById("buyer-side-card");

    var suggestUrl = form.getAttribute("data-suggest-url");
    var prefillUrlTemplate = form.getAttribute("data-prefill-url");
    var emptyLabel = form.getAttribute("data-empty-label") || "";
    var noPropertiesLabel = form.getAttribute("data-no-properties-label") || "";
    var defaultSellerRate = form.getAttribute("data-default-seller-rate") || "3";
    var defaultBuyerRate = form.getAttribute("data-default-buyer-rate") || "3";

    var debounceMs = 250;
    var debounceTimer = null;
    var suggestItems = [];
    var activeSuggestIndex = -1;
    var manualOverrides = {
        original_amount: false,
        seller_commission_rate: false,
        buyer_commission_rate: false,
        seller_vat_amount: false,
        buyer_vat_amount: false,
        currency: false,
    };

    function parseNumber(value) {
        var parsed = parseFloat(String(value || "").replace(",", "."));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function formatMoney(amount, currency) {
        var value = parseNumber(amount);
        return currency + " " + value.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function getSearchMode() {
        var checked = form.querySelector(
            'input[name="search_mode"]:checked'
        );
        return checked ? checked.value : "agent";
    }

    function getCurrency() {
        return currencySelect ? currencySelect.value : "USD";
    }

    function closeSuggestList() {
        if (!propertySearchList) {
            return;
        }
        propertySearchList.hidden = true;
        propertySearchList.innerHTML = "";
        suggestItems = [];
        activeSuggestIndex = -1;
        if (propertySearchInput) {
            propertySearchInput.setAttribute("aria-expanded", "false");
        }
    }

    function openSuggestList() {
        if (!propertySearchList) {
            return;
        }
        propertySearchList.hidden = false;
        if (propertySearchInput) {
            propertySearchInput.setAttribute("aria-expanded", "true");
        }
    }

    function renderSuggestItems(items) {
        if (!propertySearchList) {
            return;
        }

        suggestItems = items || [];
        propertySearchList.innerHTML = "";
        activeSuggestIndex = -1;

        if (!suggestItems.length) {
            var empty = document.createElement("li");
            empty.className = "autocomplete-empty";
            empty.textContent = emptyLabel;
            empty.setAttribute("role", "presentation");
            propertySearchList.appendChild(empty);
            openSuggestList();
            return;
        }

        suggestItems.forEach(function (item, index) {
            var option = document.createElement("li");
            option.className = "autocomplete-option";
            option.setAttribute("role", "option");
            option.textContent = item.label;
            option.addEventListener("mousedown", function (event) {
                event.preventDefault();
                selectProperty(item);
            });
            propertySearchList.appendChild(option);
        });

        openSuggestList();
    }

    function buildSuggestUrl(query) {
        var url = suggestUrl + (suggestUrl.indexOf("?") >= 0 ? "&" : "?")
            + "q=" + encodeURIComponent(query);
        if (getSearchMode() === "agent" && agentSelect && agentSelect.value) {
            url += "&agent_id=" + encodeURIComponent(agentSelect.value);
        }
        return url;
    }

    function fetchSuggestions(query) {
        if (!query || query.length < 1) {
            closeSuggestList();
            return;
        }

        fetch(buildSuggestUrl(query), {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("suggest failed");
            }
            return response.json();
        }).then(function (data) {
            if (propertySearchInput.value.trim() !== query) {
                return;
            }
            renderSuggestItems(Array.isArray(data) ? data : []);
        }).catch(function () {
            closeSuggestList();
        });
    }

    function resetManualOverrides() {
        Object.keys(manualOverrides).forEach(function (key) {
            manualOverrides[key] = false;
        });
    }

    function applyPrefill(data) {
        resetManualOverrides();

        if (propertyIdInput) {
            propertyIdInput.value = String(data.property_id || "");
        }
        if (propertySearchInput && data.label) {
            propertySearchInput.value = data.label;
        } else if (propertySearchInput && data.address) {
            var external = data.external_id
                ? "#" + data.external_id
                : "";
            propertySearchInput.value = (
                external + " · " + data.address
            ).replace(/^ · /, "");
        }

        if (agentSelect && data.agent_id && !agentSelect.disabled) {
            agentSelect.value = String(data.agent_id);
        }

        if (currencySelect && data.currency) {
            currencySelect.value = data.currency;
        }

        if (
            originalAmountInput
            && data.operation_value !== null
            && data.operation_value !== undefined
        ) {
            originalAmountInput.value = String(data.operation_value);
        }

        if (sellerRateInput && data.seller_commission_rate !== undefined) {
            sellerRateInput.value = String(data.seller_commission_rate);
        }
        if (buyerRateInput && data.buyer_commission_rate !== undefined) {
            buyerRateInput.value = String(data.buyer_commission_rate);
        }

        if (sellerVatInput) {
            sellerVatInput.value = "0";
        }
        if (buyerVatInput) {
            buyerVatInput.value = "0";
        }

        syncCurrencyFields();
        syncSideCards();
        updateCalculations(true);
    }

    function selectProperty(item) {
        closeSuggestList();

        if (!item || !item.id) {
            return;
        }

        var prefillUrl = prefillUrlTemplate.replace(
            "/0/",
            "/" + item.id + "/"
        );

        fetch(prefillUrl, {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("prefill failed");
            }
            return response.json();
        }).then(function (data) {
            data.label = item.label;
            applyPrefill(data);
        }).catch(function () {
            if (propertySearchHint) {
                propertySearchHint.textContent = noPropertiesLabel;
            }
        });
    }

    function syncCurrencyFields() {
        var currency = getCurrency();
        if (!exchangeRateGroup || !exchangeRateInput) {
            return;
        }

        if (currency === "ARS") {
            exchangeRateGroup.hidden = false;
            exchangeRateInput.required = true;
        } else {
            exchangeRateGroup.hidden = true;
            exchangeRateInput.required = false;
        }
    }

    function syncSideCards() {
        var sellerOn = sellerSideActive && sellerSideActive.checked;
        var buyerOn = buyerSideActive && buyerSideActive.checked;

        if (sellerCard) {
            sellerCard.classList.toggle("is-inactive", !sellerOn);
        }
        if (buyerCard) {
            buyerCard.classList.toggle("is-inactive", !buyerOn);
        }

        if (sellerRateInput) {
            sellerRateInput.disabled = !sellerOn;
        }
        if (sellerVatInput) {
            sellerVatInput.disabled = !sellerOn;
        }
        if (buyerRateInput) {
            buyerRateInput.disabled = !buyerOn;
        }
        if (buyerVatInput) {
            buyerVatInput.disabled = !buyerOn;
        }

        if (referredSideGroup) {
            referredSideGroup.hidden = !(isReferred && isReferred.checked);
        }
    }

    function suggestVat(commission) {
        var value = parseNumber(commission);
        if (value <= 0) {
            return 0;
        }
        var base60 = value * 0.6;
        var base55 = base60 * 0.55;
        var ivaExact = base55 * 0.21;
        return Math.round(ivaExact / 50) * 50;
    }

    function updateCalculations(updateVatSuggestions) {
        var currency = getCurrency();
        var operationValue = parseNumber(originalAmountInput && originalAmountInput.value);
        var sellerOn = sellerSideActive && sellerSideActive.checked;
        var buyerOn = buyerSideActive && buyerSideActive.checked;
        var sellerCommission = 0;
        var buyerCommission = 0;

        if (sellerOn) {
            sellerCommission = operationValue * parseNumber(sellerRateInput && sellerRateInput.value) / 100;
        }
        if (buyerOn) {
            buyerCommission = operationValue * parseNumber(buyerRateInput && buyerRateInput.value) / 100;
        }

        var sellerCommissionEl = document.getElementById("seller_commission_amount");
        var buyerCommissionEl = document.getElementById("buyer_commission_amount");
        if (sellerCommissionEl) {
            sellerCommissionEl.textContent = sellerOn
                ? formatMoney(sellerCommission, currency)
                : "—";
        }
        if (buyerCommissionEl) {
            buyerCommissionEl.textContent = buyerOn
                ? formatMoney(buyerCommission, currency)
                : "—";
        }

        if (updateVatSuggestions) {
            if (sellerOn && sellerVatInput && !manualOverrides.seller_vat_amount) {
                sellerVatInput.value = String(suggestVat(sellerCommission));
            }
            if (buyerOn && buyerVatInput && !manualOverrides.buyer_vat_amount) {
                buyerVatInput.value = String(suggestVat(buyerCommission));
            }
        }

        var summaryValue = document.getElementById("summary_operation_value");
        var summarySeller = document.getElementById("summary_seller_commission");
        var summaryBuyer = document.getElementById("summary_buyer_commission");
        var summaryTotal = document.getElementById("summary_total_commission");
        var summaryParticipation = document.getElementById("summary_participation");

        if (summaryValue) {
            summaryValue.textContent = formatMoney(operationValue, currency);
        }
        if (summarySeller) {
            summarySeller.textContent = sellerOn
                ? formatMoney(sellerCommission, currency)
                : "—";
        }
        if (summaryBuyer) {
            summaryBuyer.textContent = buyerOn
                ? formatMoney(buyerCommission, currency)
                : "—";
        }
        if (summaryTotal) {
            summaryTotal.textContent = formatMoney(
                sellerCommission + buyerCommission,
                currency
            );
        }
        if (summaryParticipation) {
            if (sellerOn && buyerOn) {
                summaryParticipation.textContent = form.getAttribute("data-participation-both") || "Both";
            } else if (sellerOn) {
                summaryParticipation.textContent = form.getAttribute("data-participation-seller") || "Seller";
            } else if (buyerOn) {
                summaryParticipation.textContent = form.getAttribute("data-participation-buyer") || "Buyer";
            } else {
                summaryParticipation.textContent = "—";
            }
        }
    }

    function syncSearchModeUi() {
        var mode = getSearchMode();
        if (agentFieldGroup) {
            agentFieldGroup.hidden = mode === "property_id" && agentSelect && agentSelect.disabled;
        }
        if (propertySearchHint) {
            if (mode === "agent" && agentSelect && !agentSelect.value && !agentSelect.disabled) {
                propertySearchHint.textContent = form.getAttribute("data-select-agent-first") || "";
            } else {
                propertySearchHint.textContent = "";
            }
        }
    }

    function clearPropertySelection() {
        if (propertyIdInput) {
            propertyIdInput.value = "";
        }
        if (propertySearchInput) {
            propertySearchInput.value = "";
        }
        closeSuggestList();
    }

    if (propertySearchInput) {
        propertySearchInput.addEventListener("input", function () {
            if (propertyIdInput) {
                propertyIdInput.value = "";
            }
            var query = propertySearchInput.value.trim();
            window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(function () {
                fetchSuggestions(query);
            }, debounceMs);
        });

        propertySearchInput.addEventListener("keydown", function (event) {
            if (propertySearchList && propertySearchList.hidden) {
                if (event.key === "ArrowDown" && propertySearchInput.value.trim()) {
                    fetchSuggestions(propertySearchInput.value.trim());
                }
                return;
            }

            var options = propertySearchList
                ? propertySearchList.querySelectorAll(".autocomplete-option")
                : [];

            if (event.key === "Escape") {
                closeSuggestList();
                return;
            }

            if (event.key === "ArrowDown") {
                event.preventDefault();
                activeSuggestIndex = Math.min(
                    activeSuggestIndex + 1,
                    options.length - 1
                );
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                activeSuggestIndex = Math.max(activeSuggestIndex - 1, 0);
            } else if (
                event.key === "Enter"
                && activeSuggestIndex >= 0
                && suggestItems[activeSuggestIndex]
            ) {
                event.preventDefault();
                selectProperty(suggestItems[activeSuggestIndex]);
                return;
            } else {
                return;
            }

            options.forEach(function (option, index) {
                option.classList.toggle(
                    "is-active",
                    index === activeSuggestIndex
                );
            });
        });
    }

    document.addEventListener("click", function (event) {
        if (form.contains(event.target)) {
            return;
        }
        closeSuggestList();
    });

    form.querySelectorAll('input[name="search_mode"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
            clearPropertySelection();
            syncSearchModeUi();
        });
    });

    if (agentSelect && !agentSelect.disabled) {
        agentSelect.addEventListener("change", function () {
            clearPropertySelection();
            syncSearchModeUi();
        });
    }

    if (currencySelect) {
        currencySelect.addEventListener("change", function () {
            manualOverrides.currency = true;
            syncCurrencyFields();
            updateCalculations(false);
        });
    }

    if (originalAmountInput) {
        originalAmountInput.addEventListener("input", function () {
            manualOverrides.original_amount = true;
            updateCalculations(true);
        });
    }

    if (sellerRateInput) {
        sellerRateInput.addEventListener("input", function () {
            manualOverrides.seller_commission_rate = true;
            updateCalculations(true);
        });
    }

    if (buyerRateInput) {
        buyerRateInput.addEventListener("input", function () {
            manualOverrides.buyer_commission_rate = true;
            updateCalculations(true);
        });
    }

    if (sellerVatInput) {
        sellerVatInput.addEventListener("input", function () {
            manualOverrides.seller_vat_amount = true;
        });
    }

    if (buyerVatInput) {
        buyerVatInput.addEventListener("input", function () {
            manualOverrides.buyer_vat_amount = true;
        });
    }

    [sellerSideActive, buyerSideActive, isReferred].forEach(function (input) {
        if (!input) {
            return;
        }
        input.addEventListener("change", function () {
            syncSideCards();
            updateCalculations(true);
        });
    });

    form.addEventListener("submit", function (event) {
        if (!propertyIdInput || !propertyIdInput.value) {
            event.preventDefault();
            if (propertySearchHint) {
                propertySearchHint.textContent = form.getAttribute("data-property-required") || "";
            }
            if (propertySearchInput) {
                propertySearchInput.focus();
            }
        }
    });

    syncSearchModeUi();
    syncCurrencyFields();
    syncSideCards();
    updateCalculations(false);

    if (propertyIdInput && propertyIdInput.value) {
        selectProperty({ id: parseInt(propertyIdInput.value, 10) });
    }
})();
