/**
 * JRH One — Agent current account page interactions
 */
(function () {
    var pageRoot = document.querySelector('.aa-detail') || document.body;
    var agentId = pageRoot.dataset.aaAgentId;
    var movementDialog = document.getElementById('aa-movement-dialog');
    var chargeDialog = document.getElementById('aa-charge-dialog');
    var cancelDialog = document.getElementById('aa-cancel-dialog');
    var detailDialog = document.getElementById('aa-detail-dialog');
    var filtersDialog = document.getElementById('aa-filters-dialog');
    var movementForm = document.querySelector('[data-aa-movement-form]');
    var chargeForm = document.querySelector('[data-aa-charge-form]');
    var cancelForm = document.querySelector('[data-aa-cancel-form]');
    var defaultVatRate = parseFloat(pageRoot.dataset.aaVatRate || '21') / 100;
    var pendingChargesCache = {};

    var titles = {
        payment: pageRoot.dataset.aaTitlePayment || 'Registrar pago',
        commission: pageRoot.dataset.aaTitleCommission || 'Acreditar comisión',
        adjustment: pageRoot.dataset.aaTitleAdjustment || 'Ajuste manual',
        charge: pageRoot.dataset.aaTitleCharge || 'Registrar cargo'
    };

    function closeDialog(dialog) {
        if (dialog && dialog.open) {
            dialog.close();
        }
    }

    document.querySelectorAll('[data-aa-dialog-close]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            closeDialog(btn.closest('dialog'));
        });
    });

    document.querySelectorAll('[data-aa-filters-open]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (filtersDialog) {
                filtersDialog.showModal();
            }
        });
    });

    document.querySelectorAll('[data-aa-more-toggle]').forEach(function (btn) {
        btn.addEventListener('click', function (event) {
            event.stopPropagation();
            var panel = btn.parentElement.querySelector('.aa-actions-more__panel');
            if (!panel) return;
            var open = panel.hidden;
            document.querySelectorAll('.aa-actions-more__panel').forEach(function (p) {
                p.hidden = true;
            });
            panel.hidden = !open;
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
    });

    document.addEventListener('click', function () {
        document.querySelectorAll('.aa-actions-more__panel').forEach(function (p) {
            p.hidden = true;
        });
        document.querySelectorAll('[data-aa-more-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-expanded', 'false');
        });
        document.querySelectorAll('.aa-autocomplete-list').forEach(function (list) {
            list.hidden = true;
        });
    });

    function parseMoney(value) {
        if (!value) return NaN;
        var normalized = String(value).replace(/\s/g, '').replace(/\$/g, '');
        if (normalized.indexOf(',') >= 0 && normalized.indexOf('.') >= 0) {
            normalized = normalized.replace(/\./g, '').replace(',', '.');
        } else if (normalized.indexOf(',') >= 0) {
            normalized = normalized.replace(',', '.');
        }
        return parseFloat(normalized);
    }

    function formatMoney(value, currency) {
        var formatted = value.toLocaleString('es-AR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return (currency || 'USD') + ' ' + formatted;
    }

    function roundMoney(value) {
        return Math.round((value + Number.EPSILON) * 100) / 100;
    }

    function computeVatBreakdown(amount, vatMode, vatRate) {
        var net;
        var vat;
        var gross;
        if (!amount || amount <= 0) {
            net = 0;
            vat = 0;
            gross = 0;
        } else if (vatMode === 'none') {
            net = amount;
            vat = 0;
            gross = amount;
        } else if (vatMode === 'add_vat') {
            net = amount;
            vat = roundMoney(net * vatRate);
            gross = roundMoney(net + vat);
        } else {
            gross = amount;
            if (vatRate > 0) {
                net = roundMoney(gross / (1 + vatRate));
                vat = roundMoney(gross - net);
            } else {
                net = gross;
                vat = 0;
            }
        }
        return { net: net, vat: vat, gross: gross, rate: vatRate };
    }

    function selectedVatMode(form) {
        var selected = form.querySelector('[name="vat_mode"]:checked');
        return selected ? selected.value : 'none';
    }

    function fetchTreasuryAccounts(currency, paymentMethod) {
        if (!agentId) return Promise.resolve({ accounts: [], suggested_id: null });
        var params = new URLSearchParams({ currency: currency || 'USD' });
        if (paymentMethod) params.set('payment_method', paymentMethod);
        return fetch('/agent-accounts/treasury-accounts?' + params.toString(), {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            if (!response.ok) throw new Error('treasury_accounts_failed');
            return response.json();
        }).catch(function () {
            return { accounts: [], suggested_id: null };
        });
    }

    function renderTreasuryAccountsSelect(currency, paymentMethod, selectedValue) {
        if (!movementForm) return;
        var select = movementForm.querySelector('[data-aa-treasury-account]');
        if (!select) return;
        fetchTreasuryAccounts(currency, paymentMethod).then(function (payload) {
            select.innerHTML = '';
            var auto = document.createElement('option');
            auto.value = '';
            auto.dataset.aaCurrency = currency || '';
            auto.textContent = movementForm.dataset.aaTreasuryAuto || 'Automática según medio';
            select.appendChild(auto);
            payload.accounts.forEach(function (account) {
                var option = document.createElement('option');
                option.value = String(account.id);
                option.dataset.aaCurrency = account.currency || '';
                option.textContent = account.name + ' (' + account.currency + ')';
                select.appendChild(option);
            });
            var preferred = selectedValue || payload.suggested_id;
            if (preferred) select.value = String(preferred);
            updatePaymentConfirm();
        });
    }

    function fetchPendingCharges(currency) {
        if (!agentId) return Promise.resolve([]);
        var cacheKey = currency || 'USD';
        if (pendingChargesCache[cacheKey]) {
            return Promise.resolve(pendingChargesCache[cacheKey]);
        }
        return fetch('/agent-accounts/' + agentId + '/pending-charges?currency=' + encodeURIComponent(cacheKey), {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function (response) {
            if (!response.ok) throw new Error('pending_charges_failed');
            return response.json();
        }).then(function (payload) {
            pendingChargesCache[cacheKey] = payload.charges || [];
            return pendingChargesCache[cacheKey];
        }).catch(function () {
            return [];
        });
    }

    function renderPendingChargesSelect(currency, selectedValue) {
        if (!movementForm) return;
        var select = movementForm.querySelector('[data-aa-apply-payment]');
        if (!select) return;
        fetchPendingCharges(currency).then(function (charges) {
            var generalOption = select.querySelector('option[value="general"]');
            select.innerHTML = '';
            if (generalOption) {
                select.appendChild(generalOption);
            } else {
                var base = document.createElement('option');
                base.value = 'general';
                base.textContent = 'Pago a cuenta / saldo general';
                select.appendChild(base);
            }
            charges.forEach(function (charge) {
                var option = document.createElement('option');
                option.value = String(charge.id);
                option.textContent = charge.label;
                select.appendChild(option);
            });
            if (selectedValue) {
                select.value = selectedValue;
            }
        });
    }

    function enabledControl(selector) {
        if (!movementForm) return null;
        var nodes = movementForm.querySelectorAll(selector);
        for (var i = 0; i < nodes.length; i += 1) {
            if (!nodes[i].disabled) return nodes[i];
        }
        return nodes[0] || null;
    }

    function setControlGroup(selector, enabled) {
        if (!movementForm) return;
        movementForm.querySelectorAll(selector).forEach(function (node) {
            node.querySelectorAll('input, select, textarea').forEach(function (field) {
                field.disabled = !enabled;
            });
        });
    }

    function paymentCurrency() {
        var select = movementForm.querySelector('[data-aa-payment-only] [data-aa-currency-select]');
        return select ? select.value : 'USD';
    }

    function paymentBalance(currency) {
        var raw = currency === 'ARS'
            ? movementForm.dataset.aaBalanceArs
            : movementForm.dataset.aaBalanceUsd;
        var value = parseFloat(raw);
        return isNaN(value) ? 0 : value;
    }

    function balancePhrase(amount, currency) {
        var formatted = formatMoney(Math.abs(amount), currency);
        if (Math.abs(amount) < 0.005) return formatted;
        if (amount > 0) {
            return formatted + ' ' + (movementForm.dataset.aaFavorLabel || 'a favor');
        }
        return formatted + ' ' + (movementForm.dataset.aaPendingLabel || 'pendiente');
    }

    function syncPaymentApplyOptions(currency) {
        var select = movementForm.querySelector('[data-aa-apply-payment]');
        if (!select) return;
        var selectedHidden = false;
        Array.prototype.forEach.call(select.options, function (option) {
            var optionCurrency = option.getAttribute('data-aa-currency');
            var hide = optionCurrency && optionCurrency !== currency;
            option.hidden = !!hide;
            option.disabled = !!hide;
            if (hide && option.selected) selectedHidden = true;
        });
        if (selectedHidden) select.value = 'general';
    }

    function syncPaymentReference(force) {
        var reference = movementForm.querySelector('[data-aa-payment-reference]');
        if (!reference) return;
        if (force) reference.dataset.aaUserEdited = '';
        if (reference.dataset.aaUserEdited === '1') return;
        var apply = movementForm.querySelector('[data-aa-apply-payment]');
        var selected = apply && apply.selectedOptions[0];
        var suggestion = (selected && selected.dataset.aaPaymentRef)
            || movementForm.dataset.aaPaymentRefDefault
            || '';
        var previous = reference.dataset.aaSuggested || '';
        if (force || reference.value === previous || !reference.value.trim()) {
            reference.value = suggestion;
            reference.dataset.aaSuggested = suggestion;
        }
    }

    function prefillPayoutAmount() {
        var input = movementForm.querySelector('[data-aa-payment-only] [data-aa-amount-input]');
        if (!input) return;
        input.dataset.aaUserEdited = '';
        var balance = paymentBalance(paymentCurrency());
        input.value = balance > 0.004
            ? balance.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '';
    }

    function paymentBlockReason(amount, balance) {
        if (balance <= 0.004) return movementForm.dataset.aaNoBalanceMessage || '';
        if (amount - balance > 0.0001) return movementForm.dataset.aaExceedsMessage || '';
        return '';
    }

    function syncMovementForm(type) {
        if (!movementForm || !movementDialog) return;
        var isPayment = type === 'payment';
        var typeInput = movementForm.querySelector('[data-aa-movement-type-input]');
        var titleEl = movementForm.querySelector('[data-aa-form-title]');
        if (typeInput) typeInput.value = type;
        if (titleEl) titleEl.textContent = titles[type] || titles.payment;

        movementDialog.classList.toggle('aa-payment-modal', isPayment);
        movementForm.querySelectorAll('[data-aa-payment-intro]').forEach(function (node) {
            node.hidden = !isPayment;
        });
        var paymentOnly = movementForm.querySelector('[data-aa-payment-only]');
        if (paymentOnly) paymentOnly.hidden = !isPayment;
        setControlGroup('[data-aa-payment-only]', isPayment);
        setControlGroup('[data-aa-shared]', !isPayment);
        movementForm.querySelectorAll('[data-aa-shared]').forEach(function (node) {
            node.hidden = isPayment;
        });

        movementForm.querySelectorAll('[data-aa-section]').forEach(function (section) {
            var sectionType = section.getAttribute('data-aa-section');
            section.hidden = sectionType !== type;
        });

        var adjustmentSection = movementForm.querySelector('[data-aa-section="adjustment"]');
        if (type === 'adjustment' && adjustmentSection) {
            adjustmentSection.hidden = false;
        }

        movementForm.querySelectorAll('[name="description"]').forEach(function (field) {
            if (field.disabled) return;
            field.required = type === 'adjustment';
        });

        var currencySelect = enabledControl('[data-aa-currency-select]');
        var currency = currencySelect ? currencySelect.value : 'USD';
        movementForm.querySelectorAll('[data-aa-currency-prefix]').forEach(function (prefix) {
            prefix.textContent = currency;
        });

        var sharedFx = movementForm.querySelector('[data-aa-fx-block]');
        if (sharedFx) {
            sharedFx.hidden = !(currency === 'USD' && type === 'commission');
        }
        syncPaymentFx();

        var submit = movementForm.querySelector('[data-aa-payment-submit]');
        if (submit) {
            submit.textContent = isPayment
                ? (movementForm.dataset.aaSubmitPayment || 'Registrar pago')
                : (movementForm.dataset.aaSubmitDefault || 'Confirmar');
            submit.disabled = false;
        }
        movementForm.dataset.aaSubmitting = '';

        if (isPayment) {
            syncPaymentApplyOptions(currency);
            syncPaymentReference(false);
            var paymentMethod = movementForm.querySelector('[data-aa-payment-method]');
            renderTreasuryAccountsSelect(
                currency,
                paymentMethod ? paymentMethod.value : ''
            );
            updatePaymentConfirm();
        }
    }

    function treasuryOptionCurrency() {
        var select = movementForm.querySelector('[data-aa-treasury-account]');
        if (!select) return '';
        var option = select.options[select.selectedIndex];
        if (!option) return '';
        return (option.dataset.aaCurrency || '').toUpperCase();
    }

    function syncPaymentFx() {
        var paymentFx = movementForm.querySelector('[data-aa-payment-fx]');
        if (!paymentFx) return;
        var typeInput = movementForm.querySelector('[data-aa-movement-type-input]');
        var isPayment = typeInput && typeInput.value === 'payment';
        var paymentCur = paymentCurrency();
        var sourceCur = treasuryOptionCurrency();
        var needsFx = !!(isPayment && sourceCur && paymentCur && sourceCur !== paymentCur);
        paymentFx.hidden = !needsFx;
        paymentFx.querySelectorAll('input, select, textarea').forEach(function (field) {
            field.disabled = !needsFx;
        });
    }

    function updatePaymentConfirm() {
        if (!movementForm) return;
        syncPaymentFx();
        var confirmBlock = movementForm.querySelector('[data-aa-payment-confirm]');
        if (!confirmBlock) return;

        var currency = paymentCurrency();
        var amountInput = movementForm.querySelector('[data-aa-payment-only] [data-aa-amount-input]');
        var paymentMethod = movementForm.querySelector('[data-aa-payment-method]');
        var amount = parseMoney(amountInput ? amountInput.value : '');
        var balance = paymentBalance(currency);
        var after = roundMoney(balance - (amount > 0 ? amount : 0));
        var methodValue = paymentMethod ? paymentMethod.value : '';

        var currentEl = confirmBlock.querySelector('[data-aa-confirm-current]');
        var paymentEl = confirmBlock.querySelector('[data-aa-confirm-payment]');
        var afterEl = confirmBlock.querySelector('[data-aa-confirm-after]');
        var cashEl = confirmBlock.querySelector('[data-aa-confirm-cash]');
        var methodEl = confirmBlock.querySelector('[data-aa-confirm-method]');
        var balanceLine = movementForm.querySelector('[data-aa-payment-balance-line]');
        var errorEl = movementForm.querySelector('[data-aa-payment-error]');
        var submit = movementForm.querySelector('[data-aa-payment-submit]');
        var reason = paymentBlockReason(amount, balance, methodValue);

        if (balanceLine) balanceLine.textContent = balancePhrase(balance, currency);
        if (currentEl) currentEl.textContent = balancePhrase(balance, currency);
        if (paymentEl) paymentEl.textContent = amount > 0 ? formatMoney(amount, currency) : '—';
        if (afterEl) afterEl.textContent = amount > 0 ? balancePhrase(after, currency) : '—';
        if (cashEl) cashEl.textContent = amount > 0 ? formatMoney(amount, currency) : '—';
        if (methodEl && paymentMethod) {
            var methodOption = paymentMethod.options[paymentMethod.selectedIndex];
            methodEl.textContent = methodOption && methodOption.value
                ? methodOption.textContent
                : '—';
        }
        var treasuryEl = confirmBlock.querySelector('[data-aa-confirm-treasury]');
        var treasurySelect = movementForm.querySelector('[data-aa-treasury-account]');
        if (treasuryEl && treasurySelect) {
            var treasuryOption = treasurySelect.options[treasurySelect.selectedIndex];
            treasuryEl.textContent = treasuryOption
                ? treasuryOption.textContent
                : (movementForm.dataset.aaTreasuryAuto || '—');
        }
        if (errorEl) {
            errorEl.hidden = !reason;
            errorEl.textContent = reason;
        }
        if (submit && movementForm.querySelector('[data-aa-movement-type-input]').value === 'payment') {
            submit.disabled = !!reason || !(amount > 0) || !methodValue || movementForm.dataset.aaSubmitting === '1';
        }
    }

    document.querySelectorAll('[data-aa-open-movement]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!movementDialog) return;
            var type = btn.getAttribute('data-aa-open-movement') || 'payment';
            if (movementDialog.parentElement !== document.body) {
                document.body.appendChild(movementDialog);
            }
            if (type === 'payment') prefillPayoutAmount();
            syncMovementForm(type);
            if (type === 'payment') syncPaymentReference(true);
            movementDialog.showModal();
        });
    });

    document.querySelectorAll('[data-aa-open-charge]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!chargeDialog) return;
            if (chargeDialog.parentElement !== document.body) {
                document.body.appendChild(chargeDialog);
            }
            syncChargeForm();
            chargeDialog.showModal();
        });
    });

    function syncRecurrenceVisibility() {
        if (!chargeForm) return;
        var switchInput = chargeForm.querySelector('[data-aa-recurring-switch]');
        var recurrenceField = chargeForm.querySelector('[data-aa-recurrence-field]');
        if (!switchInput || !recurrenceField) return;
        recurrenceField.hidden = !switchInput.checked;
    }

    function syncChargeForm() {
        if (!chargeForm) return;
        var category = chargeForm.querySelector('[data-aa-charge-category]');
        var categoryValue = category ? category.value : 'fee';
        var isOther = categoryValue === 'other';
        var isJrh = categoryValue === 'jrh_subscription';
        var recurrenceType = chargeForm.querySelector('[data-aa-recurrence-type]');
        var recurringSwitch = chargeForm.querySelector('[data-aa-recurring-switch]');

        chargeForm.querySelectorAll('[data-aa-charge-section="description"]').forEach(function (section) {
            var textarea = section.querySelector('[data-aa-charge-description]');
            if (textarea) {
                textarea.required = isOther;
            }
            section.hidden = !isOther;
        });

        chargeForm.querySelectorAll('[data-aa-charge-section="jrh"]').forEach(function (section) {
            section.hidden = !isJrh;
        });

        var periodInput = chargeForm.querySelector('[data-aa-billing-period]');
        var periodRequired = chargeForm.querySelector('[data-aa-period-required]');
        var isFee = categoryValue === 'fee';
        if (periodInput) {
            periodInput.required = isFee;
        }
        if (periodRequired) {
            periodRequired.hidden = !isFee;
        }

        if (isJrh && recurrenceType) {
            recurrenceType.value = 'monthly';
        }

        if (isJrh && recurringSwitch && !recurringSwitch.dataset.userTouched) {
            recurringSwitch.checked = true;
        }

        syncRecurrenceVisibility();
        updateChargeVatSummary();
        syncFeeReference();
    }

    function syncFeeReference() {
        if (!chargeForm) return;
        var reference = chargeForm.querySelector('[data-aa-charge-reference]');
        var category = chargeForm.querySelector('[data-aa-charge-category]');
        var periodInput = chargeForm.querySelector('[data-aa-billing-period]');
        if (!reference || !category || !periodInput) return;
        if (reference.dataset.aaUserEdited === '1') return;

        var previous = reference.dataset.aaSuggested || '';
        var period = periodInput.value.trim();
        var prefix = chargeForm.dataset.aaFeeReferencePrefix || 'Fee mensual';
        var suggestion = (
            category.value === 'fee' && period
        ) ? prefix + ' · ' + period : '';

        if (reference.value === previous || !reference.value.trim()) {
            reference.value = suggestion;
            reference.dataset.aaSuggested = suggestion;
        }
    }

    function updateChargeVatSummary() {
        if (!chargeForm) return;
        var amountInput = chargeForm.querySelector('[data-aa-charge-amount]');
        var summary = chargeForm.querySelector('[data-aa-vat-summary]');
        var currencySelect = chargeForm.querySelector('[data-aa-charge-currency]');
        var amountLabel = chargeForm.querySelector('[data-aa-amount-label]');
        var vatMode = selectedVatMode(chargeForm);
        var currency = currencySelect ? currencySelect.value : 'USD';
        var amount = parseMoney(amountInput ? amountInput.value : '');
        var breakdown = computeVatBreakdown(amount, vatMode, defaultVatRate);

        if (amountLabel) {
            if (vatMode === 'gross_includes_vat') {
                amountLabel.textContent = chargeForm.dataset.labelGross || 'Importe final';
            } else if (vatMode === 'add_vat') {
                amountLabel.textContent = chargeForm.dataset.labelNet || 'Importe neto';
            } else {
                amountLabel.textContent = chargeForm.dataset.labelAmount || 'Importe';
            }
        }

        if (summary) summary.hidden = false;
        var netEl = summary ? summary.querySelector('[data-aa-vat-net]') : null;
        var vatEl = summary ? summary.querySelector('[data-aa-vat-amount]') : null;
        var grossEl = summary ? summary.querySelector('[data-aa-vat-gross]') : null;
        var rateLabel = summary ? summary.querySelector('[data-aa-vat-rate-label]') : null;
        if (netEl) netEl.textContent = amount > 0 ? formatMoney(breakdown.net, currency) : '—';
        if (vatEl) vatEl.textContent = amount > 0 ? formatMoney(breakdown.vat, currency) : '—';
        if (grossEl) grossEl.textContent = amount > 0 ? formatMoney(breakdown.gross, currency) : '—';
        if (rateLabel) {
            rateLabel.textContent = 'IVA ' + Math.round(defaultVatRate * 100) + '%';
        }
    }

    if (movementForm) {
        function updateFxEquivalent() {
            var type = movementForm.querySelector('[data-aa-movement-type-input]').value;
            var scope = type === 'payment'
                ? movementForm.querySelector('[data-aa-payment-fx]')
                : movementForm.querySelector('[data-aa-fx-block]');
            if (!scope || scope.hidden) return;
            var fxRate = scope.querySelector('[data-aa-fx-rate]');
            var fxEquivalent = scope.querySelector('[data-aa-fx-equivalent]');
            var amountInput = enabledControl('[data-aa-amount-input]');
            if (!fxRate || !fxEquivalent || !amountInput || fxRate.disabled) return;
            var rate = parseMoney(fxRate.value);
            var amount = parseMoney(amountInput.value);
            if (!rate || !amount) {
                fxEquivalent.hidden = true;
                return;
            }
            var equivalent = roundMoney(amount * rate);
            fxEquivalent.textContent = 'Equivalente ARS ' + equivalent.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            fxEquivalent.hidden = false;
        }

        movementForm.addEventListener('input', function (event) {
            if (event.target.matches('[data-aa-amount-input]')) {
                event.target.dataset.aaUserEdited = '1';
                updateFxEquivalent();
                updatePaymentConfirm();
            }
            if (event.target.matches('[data-aa-fx-rate]')) updateFxEquivalent();
            if (event.target.matches('[data-aa-payment-reference]')) {
                var suggested = event.target.dataset.aaSuggested || '';
                event.target.dataset.aaUserEdited = event.target.value === suggested ? '' : '1';
            }
        });

        movementForm.addEventListener('change', function (event) {
            if (event.target.matches('[data-aa-currency-select]')) {
                var type = movementForm.querySelector('[data-aa-movement-type-input]').value;
                if (type === 'payment') {
                    var amountInput = movementForm.querySelector('[data-aa-payment-only] [data-aa-amount-input]');
                    if (amountInput && amountInput.dataset.aaUserEdited !== '1') {
                        prefillPayoutAmount();
                    }
                }
                syncMovementForm(type);
                updateFxEquivalent();
            }
            if (event.target.matches('[data-aa-apply-payment]')) {
                syncPaymentReference(false);
                updatePaymentConfirm();
            }
            if (event.target.matches('[data-aa-payment-method]')) {
                renderTreasuryAccountsSelect(paymentCurrency(), event.target.value);
                updatePaymentConfirm();
            }
            if (event.target.matches('[data-aa-treasury-account]')) updatePaymentConfirm();
        });

        movementForm.addEventListener('submit', function (event) {
            if (movementForm.dataset.aaSubmitting === '1') {
                event.preventDefault();
                return;
            }
            var type = movementForm.querySelector('[data-aa-movement-type-input]').value;
            if (type === 'payment') {
                var amount = parseMoney(
                    (movementForm.querySelector('[data-aa-payment-only] [data-aa-amount-input]') || {}).value
                );
                var method = movementForm.querySelector('[data-aa-payment-method]');
                var reason = paymentBlockReason(amount, paymentBalance(paymentCurrency()));
                if (reason || !(amount > 0) || !(method && method.value)) {
                    event.preventDefault();
                    updatePaymentConfirm();
                    if (!reason && !(method && method.value)) {
                        var errorEl = movementForm.querySelector('[data-aa-payment-error]');
                        if (errorEl) {
                            errorEl.hidden = false;
                            errorEl.textContent = movementForm.dataset.aaMethodMessage || '';
                        }
                    }
                    return;
                }
            }
            movementForm.dataset.aaSubmitting = '1';
            var submit = movementForm.querySelector('[data-aa-payment-submit]');
            if (submit) submit.disabled = true;
        });

        initOperationAutocomplete();
    }

    function initOperationAutocomplete() {
        if (!movementForm || !agentId) return;
        var input = movementForm.querySelector('[data-aa-operation-input]');
        var hiddenId = movementForm.querySelector('[data-aa-operation-id]');
        var list = movementForm.querySelector('[data-aa-operation-list]');
        if (!input || !list) return;

        var debounceTimer = null;
        var activeIndex = -1;

        function clearSelection() {
            if (hiddenId) hiddenId.value = '';
        }

        function renderOptions(operations) {
            list.innerHTML = '';
            if (!operations.length) {
                var empty = document.createElement('div');
                empty.className = 'aa-autocomplete-empty';
                empty.textContent = 'Sin operaciones';
                list.appendChild(empty);
                list.hidden = false;
                return;
            }
            operations.forEach(function (operation, index) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'aa-autocomplete-option';
                btn.textContent = operation.label;
                btn.dataset.index = String(index);
                btn.addEventListener('click', function (event) {
                    event.stopPropagation();
                    input.value = operation.display_id;
                    if (hiddenId) hiddenId.value = String(operation.id);
                    list.hidden = true;
                });
                list.appendChild(btn);
            });
            list.hidden = false;
            activeIndex = -1;
        }

        function searchOperations(query) {
            fetch('/agent-accounts/' + agentId + '/operations/search?q=' + encodeURIComponent(query || ''), {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (response) {
                if (!response.ok) throw new Error('search_failed');
                return response.json();
            }).then(function (payload) {
                renderOptions(payload.operations || []);
            }).catch(function () {
                renderOptions([]);
            });
        }

        input.addEventListener('input', function () {
            clearSelection();
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {
                searchOperations(input.value.trim());
            }, 220);
        });

        input.addEventListener('focus', function () {
            searchOperations(input.value.trim());
        });

        input.addEventListener('keydown', function (event) {
            var options = list.querySelectorAll('.aa-autocomplete-option');
            if (!options.length || list.hidden) return;
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                activeIndex = Math.min(activeIndex + 1, options.length - 1);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                activeIndex = Math.max(activeIndex - 1, 0);
            } else if (event.key === 'Enter' && activeIndex >= 0) {
                event.preventDefault();
                options[activeIndex].click();
                return;
            } else if (event.key === 'Escape') {
                list.hidden = true;
                return;
            } else {
                return;
            }
            options.forEach(function (option, index) {
                option.classList.toggle('is-active', index === activeIndex);
            });
        });
    }

    if (chargeForm) {
        var chargeCurrency = chargeForm.querySelector('[data-aa-charge-currency]');
        var chargePrefix = chargeForm.querySelector('[data-aa-charge-currency-prefix]');
        var chargeFx = chargeForm.querySelector('[data-aa-charge-fx]');
        var chargeFxRate = chargeForm.querySelector('[data-aa-charge-fx-rate]');
        var chargeFxEquivalent = chargeForm.querySelector('[data-aa-charge-fx-equivalent]');
        var chargeAmount = chargeForm.querySelector('[data-aa-charge-amount]');
        var chargeCategory = chargeForm.querySelector('[data-aa-charge-category]');
        var recurringSwitch = chargeForm.querySelector('[data-aa-recurring-switch]');

        function updateChargeFx() {
            if (!chargeFx || !chargeCurrency) return;
            chargeFx.hidden = chargeCurrency.value !== 'USD';
            updateChargeFxEquivalent();
        }

        function updateChargeFxEquivalent() {
            if (!chargeFxRate || !chargeFxEquivalent || !chargeAmount) return;
            var rate = parseMoney(chargeFxRate.value);
            var amount = parseMoney(chargeAmount.value);
            var breakdown = computeVatBreakdown(amount, selectedVatMode(chargeForm), defaultVatRate);
            if (!rate || !breakdown.gross) {
                chargeFxEquivalent.hidden = true;
                return;
            }
            var equivalent = roundMoney(breakdown.gross * rate);
            chargeFxEquivalent.textContent = 'Equivalente ARS ' + equivalent.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            chargeFxEquivalent.hidden = false;
        }

        if (chargeCurrency) {
            chargeCurrency.addEventListener('change', function () {
                if (chargePrefix) chargePrefix.textContent = chargeCurrency.value;
                updateChargeFx();
                updateChargeVatSummary();
            });
        }
        if (chargeCategory) {
            chargeCategory.addEventListener('change', syncChargeForm);
        }
        if (recurringSwitch) {
            recurringSwitch.addEventListener('change', function () {
                recurringSwitch.dataset.userTouched = '1';
                syncRecurrenceVisibility();
            });
        }
        chargeForm.querySelectorAll('[data-aa-vat-mode]').forEach(function (input) {
            input.addEventListener('change', function () {
                updateChargeVatSummary();
                updateChargeFxEquivalent();
            });
        });
        if (chargeAmount) chargeAmount.addEventListener('input', function () {
            updateChargeVatSummary();
            updateChargeFxEquivalent();
        });
        if (chargeFxRate) chargeFxRate.addEventListener('input', updateChargeFxEquivalent);
        var chargePeriod = chargeForm.querySelector('[data-aa-billing-period]');
        var chargeReference = chargeForm.querySelector('[data-aa-charge-reference]');
        if (chargePeriod) chargePeriod.addEventListener('input', syncFeeReference);
        if (chargeReference) {
            chargeReference.addEventListener('input', function () {
                if (chargeReference.value === (chargeReference.dataset.aaSuggested || '')) {
                    delete chargeReference.dataset.aaUserEdited;
                } else {
                    chargeReference.dataset.aaUserEdited = '1';
                }
            });
        }

        updateChargeFx();
        syncChargeForm();
    }

    document.querySelectorAll('[data-aa-detail-open]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!detailDialog) return;
            var headline = detailDialog.querySelector('[data-aa-detail-headline]');
            var meta = detailDialog.querySelector('[data-aa-detail-meta]');
            var list = detailDialog.querySelector('[data-aa-detail-list]');
            if (headline) headline.textContent = btn.getAttribute('data-movement-title') || '';
            if (meta) meta.textContent = btn.getAttribute('data-movement-meta') || '';
            if (list) {
                list.innerHTML = '';
                var detailRaw = btn.getAttribute('data-movement-detail') || '[]';
                try {
                    var rows = JSON.parse(detailRaw);
                    rows.forEach(function (row) {
                        var wrapper = document.createElement('div');
                        var dt = document.createElement('dt');
                        var dd = document.createElement('dd');
                        dt.textContent = row.label || '';
                        dd.textContent = row.value || '';
                        wrapper.appendChild(dt);
                        wrapper.appendChild(dd);
                        list.appendChild(wrapper);
                    });
                } catch (error) {
                    list.innerHTML = '';
                }
            }
            detailDialog.showModal();
        });
    });

    document.querySelectorAll('[data-aa-cancel-open]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!cancelDialog || !cancelForm) return;
            var id = btn.getAttribute('data-movement-id');
            cancelForm.action = '/agent-accounts/movements/' + id + '/cancel';
            cancelForm.querySelector('[data-aa-cancel-label]').textContent = btn.getAttribute('data-movement-label') || '';
            cancelForm.querySelector('[data-aa-cancel-amount]').textContent = btn.getAttribute('data-movement-amount') || '';
            cancelDialog.showModal();
        });
    });

    document.querySelectorAll('[data-aa-menu-toggle]').forEach(function (btn) {
        btn.addEventListener('click', function (event) {
            event.stopPropagation();
            var panel = btn.parentElement.querySelector('.aa-row-menu__panel');
            if (!panel) return;
            var open = !panel.hidden;
            document.querySelectorAll('.aa-row-menu__panel').forEach(function (p) { p.hidden = true; });
            panel.hidden = open;
        });
    });

    document.addEventListener('click', function () {
        document.querySelectorAll('.aa-row-menu__panel').forEach(function (p) { p.hidden = true; });
    });
})();
