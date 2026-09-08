(function () {
    var fallback = document.querySelector("[data-picker-fallback]");
    var available = document.querySelector("[data-picker-available]");
    var pickButton = document.querySelector("[data-pick-contacts]");
    var form = document.querySelector("[data-picker-form]");
    var payload = document.querySelector("[data-selected-json]");

    function supportsPicker() {
        return Boolean(
            navigator.contacts &&
            window.ContactsManager &&
            typeof navigator.contacts.select === "function"
        );
    }

    if (supportsPicker()) {
        if (fallback) {
            fallback.hidden = true;
        }
        if (available) {
            available.hidden = false;
        }
    }

    if (!pickButton || !form || !payload) {
        return;
    }

    pickButton.addEventListener("click", function () {
        if (!supportsPicker()) {
            return;
        }
        navigator.contacts
            .select(["name", "tel", "email"], { multiple: true })
            .then(function (contacts) {
                payload.value = JSON.stringify(contacts || []);
                form.submit();
            })
            .catch(function () {
                payload.value = "[]";
            });
    });
})();
