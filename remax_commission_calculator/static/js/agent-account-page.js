/**
 * JRH One — Agent current account page interactions
 */
(function () {
    var pageRoot = document.querySelector('.aa-detail') || document.body;
    var movementDialog = document.getElementById('aa-movement-dialog');
    var chargeDialog = document.getElementById('aa-charge-dialog');
    var cancelDialog = document.getElementById('aa-cancel-dialog');
    var filtersDialog = document.getElementById('aa-filters-dialog');
    var movementForm = document.querySelector('[data-aa-movement-form]');
    var chargeForm = document.querySelector('[data-aa-charge-form]');
    var cancelForm = document.querySelector('[data-aa-cancel-form]');
    var defaultVatRate = parseFloat(pageRoot.dataset.aaVatRate || '21') / 100;

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
        if (!amount || amount <= 0) {
            return null;
        }
        var net;
        var vat;
        var gross;
        if (vatMode === 'none') {
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

    function syncMovementForm(type) {
        if (!movementForm) return;
        var typeInput = movementForm.querySelector('[data-aa-movement-type-input]');
        var titleEl = movementForm.querySelector('[data-aa-form-title]');
        if (typeInput) typeInput.value = type;
        if (titleEl) titleEl.textContent = titles[type] || titles.payment;

        movementForm.querySelectorAll('[data-aa-section]').forEach(function (section) {
            section.hidden = section.getAttribute('data-aa-section') !== type;
        });

        var adjustmentSection = movementForm.querySelector('[data-aa-section="adjustment"]');
        if (type === 'adjustment' && adjustmentSection) {
            adjustmentSection.hidden = false;
        }

        var descriptionField = movementForm.querySelector('[name="description"]');
        if (descriptionField) {
            descriptionField.required = type === 'adjustment';
        }

        var fxBlock = movementForm.querySelector('[data-aa-fx-block]');
        var currencySelect = movementForm.querySelector('[data-aa-currency-select]');
        if (fxBlock && currencySelect) {
            var showFx = currencySelect.value === 'USD' && ['payment', 'commission'].indexOf(type) >= 0;
            fxBlock.hidden = !showFx;
        }
    }

    document.querySelectorAll('[data-aa-open-movement]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!movementDialog) return;
            syncMovementForm(btn.getAttribute('data-aa-open-movement') || 'payment');
            movementDialog.showModal();
        });
    });

    document.querySelectorAll('[data-aa-open-charge]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!chargeDialog) return;
            syncChargeForm();
            chargeDialog.showModal();
        });
    });

    function syncChargeForm() {
        if (!chargeForm) return;
        var category = chargeForm.querySelector('[data-aa-charge-category]');
        var categoryValue = category ? category.value : 'fee';
        var isOther = categoryValue === 'other';
        var isJrh = categoryValue === 'jrh_subscription';

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

        updateChargeVatSummary();
    }

    function updateChargeVatSummary() {
        if (!chargeForm) return;
        var amountInput = chargeForm.querySelector('[data-aa-charge-amount]');
        var summary = chargeForm.querySelector('[data-aa-vat-summary]');
        var currencySelect = chargeForm.querySelector('[data-aa-charge-currency]');
        var submitBtn = chargeForm.querySelector('[data-aa-charge-submit]');
        var amountLabel = chargeForm.querySelector('[data-aa-amount-label]');
        var vatMode = selectedVatMode(chargeForm);
        var currency = currencySelect ? currencySelect.value : 'USD';
        var amount = parseMoney(amountInput ? amountInput.value : '');
        var breakdown = computeVatBreakdown(amount, vatMode, defaultVatRate);

        if (amountLabel) {
            if (vatMode === 'gross_includes_vat') {
                amountLabel.textContent = chargeForm.dataset.labelGross || 'Importe final (IVA incluido)';
            } else if (vatMode === 'add_vat') {
                amountLabel.textContent = chargeForm.dataset.labelNet || 'Importe neto';
            } else {
                amountLabel.textContent = chargeForm.dataset.labelAmount || 'Importe';
            }
        }

        if (!breakdown || !summary) {
            if (summary) summary.hidden = true;
            if (submitBtn) submitBtn.textContent = titles.charge;
            return;
        }

        summary.hidden = false;
        var netEl = summary.querySelector('[data-aa-vat-net]');
        var vatEl = summary.querySelector('[data-aa-vat-amount]');
        var grossEl = summary.querySelector('[data-aa-vat-gross]');
        var rateLabel = summary.querySelector('[data-aa-vat-rate-label]');
        if (netEl) netEl.textContent = formatMoney(breakdown.net, currency);
        if (vatEl) vatEl.textContent = formatMoney(breakdown.vat, currency);
        if (grossEl) grossEl.textContent = formatMoney(breakdown.gross, currency);
        if (rateLabel) {
            rateLabel.textContent = 'IVA ' + Math.round(defaultVatRate * 100) + '%';
        }
        if (submitBtn) {
            submitBtn.textContent = titles.charge + ' ' + formatMoney(breakdown.gross, currency);
        }
    }

    if (movementForm) {
        var currencySelect = movementForm.querySelector('[data-aa-currency-select]');
        var prefix = movementForm.querySelector('[data-aa-currency-prefix]');
        if (currencySelect && prefix) {
            currencySelect.addEventListener('change', function () {
                prefix.textContent = currencySelect.value;
                syncMovementForm(movementForm.querySelector('[data-aa-movement-type-input]').value);
            });
        }

        var fxRate = movementForm.querySelector('[data-aa-fx-rate]');
        var fxEquivalent = movementForm.querySelector('[data-aa-fx-equivalent]');
        var amountInput = movementForm.querySelector('[data-aa-amount-input]');

        function updateFxEquivalent() {
            if (!fxRate || !fxEquivalent || !amountInput) return;
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

        if (fxRate) fxRate.addEventListener('input', updateFxEquivalent);
        if (amountInput) amountInput.addEventListener('input', updateFxEquivalent);
    }

    if (chargeForm) {
        var chargeCurrency = chargeForm.querySelector('[data-aa-charge-currency]');
        var chargePrefix = chargeForm.querySelector('[data-aa-charge-currency-prefix]');
        var chargeFx = chargeForm.querySelector('[data-aa-charge-fx]');
        var chargeFxRate = chargeForm.querySelector('[data-aa-charge-fx-rate]');
        var chargeFxEquivalent = chargeForm.querySelector('[data-aa-charge-fx-equivalent]');
        var chargeAmount = chargeForm.querySelector('[data-aa-charge-amount]');
        var chargeCategory = chargeForm.querySelector('[data-aa-charge-category]');

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
            if (!rate || !breakdown) {
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

        updateChargeFx();
        syncChargeForm();
    }

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
