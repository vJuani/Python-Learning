document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-ds-ai]").forEach(function (form) {
        var input = form.querySelector("[data-jrh-ask-input]");
        form.querySelectorAll("[data-jrh-example]").forEach(function (chip) {
            chip.addEventListener("click", function () {
                if (!input) {
                    return;
                }
                input.value = chip.getAttribute("data-jrh-example") || "";
                input.focus();
            });
        });
    });
});
