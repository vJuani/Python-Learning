(function () {
    function bindVoice(button) {
        var form = button.closest("form") || document.getElementById("agenda-ia-form");
        var input = form && (
            form.querySelector("textarea[name='note']")
            || form.querySelector("input[name='prompt']")
        );
        var homeReview = document.querySelector("[data-jrh-voice-review]");
        var isHome = Boolean(form && form.hasAttribute("data-jrh-interpret"));
        if (!window.JRH || !window.JRH.transcribeVoice) {
            return;
        }
        window.JRH.transcribeVoice({
            button: button,
            form: form,
            input: input,
            status: document.querySelector("[data-jrh-voice-status], [data-agenda-voice-status]"),
            unsupported: document.querySelector("[data-jrh-voice-unsupported]"),
            review: isHome ? homeReview : null,
            transcript: document.querySelector("[data-jrh-transcript]"),
            rerecord: document.querySelector("[data-jrh-rerecord]"),
            autoSubmit: !isHome,
        });
    }

    document.querySelectorAll("[data-agenda-voice], [data-jrh-voice]").forEach(bindVoice);

    if (document.querySelector("[data-agenda-autofocus-voice]")) {
        var auto = document.querySelector("[data-agenda-voice], [data-jrh-voice]");
        if (auto) {
            auto.click();
        }
    }

    function showProcessing() {
        var banner = document.querySelector("[data-agenda-processing]");
        if (banner) {
            banner.hidden = false;
        }
    }

    document.querySelectorAll("[data-agenda-interpret], [data-jrh-interpret]").forEach(function (form) {
        form.addEventListener("submit", showProcessing);
    });

    var fab = document.querySelector("[data-jrh-fab]");
    var prompt = document.getElementById("jrh-prompt");
    if (fab && prompt) {
        fab.addEventListener("click", function (event) {
            event.preventDefault();
            prompt.focus();
            var mic = document.querySelector("[data-jrh-voice]");
            if (mic) {
                mic.click();
            }
        });
    }

    document.querySelectorAll("[data-jrh-example]").forEach(function (chip) {
        chip.addEventListener("click", function () {
            if (!prompt) {
                return;
            }
            prompt.value = chip.getAttribute("data-jrh-example") || chip.textContent.trim();
            prompt.focus();
        });
    });
})();
