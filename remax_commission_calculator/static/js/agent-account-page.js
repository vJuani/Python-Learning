/**
 * JRH One — Agent current account page interactions
 */
(function () {
    var movementDialog = document.getElementById('aa-movement-dialog');
    var cancelDialog = document.getElementById('aa-cancel-dialog');
    var filtersDialog = document.getElementById('aa-filters-dialog');
    var movementForm = document.querySelector('[data-aa-movement-form]');
    var cancelForm = document.querySelector('[data-aa-cancel-form]');

    var titles = {
        payment: document.body.dataset.aaTitlePayment || 'Registrar pago',
        fee: document.body.dataset.aaTitleFee || 'Registrar fee',
        commission: document.body.dataset.aaTitleCommission || 'Acreditar comisión',
        adjustment: document.body.dataset.aaTitleAdjustment || 'Nuevo ajuste',
        charge: document.body.dataset.aaTitleCharge || 'Registrar cargo',
        credit: document.body.dataset.aaTitleCredit || 'Registrar crédito'
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

        var fxBlock = movementForm.querySelector('[data-aa-fx-block]');
        var currencySelect = movementForm.querySelector('[data-aa-currency-select]');
        if (fxBlock && currencySelect) {
            var showFx = currencySelect.value === 'USD' && ['payment', 'fee', 'commission'].indexOf(type) >= 0;
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
            var rate = parseFloat(String(fxRate.value).replace(/\./g, '').replace(',', '.'));
            var amount = parseFloat(String(amountInput.value).replace(/\./g, '').replace(',', '.'));
            if (!rate || !amount) {
                fxEquivalent.hidden = true;
                return;
            }
            var equivalent = amount * rate;
            fxEquivalent.textContent = 'Equivalente ARS ' + equivalent.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            fxEquivalent.hidden = false;
        }

        if (fxRate) fxRate.addEventListener('input', updateFxEquivalent);
        if (amountInput) amountInput.addEventListener('input', updateFxEquivalent);
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
