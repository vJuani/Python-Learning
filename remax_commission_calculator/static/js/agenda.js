(function () {
    function bindVoice(button) {
        var form = button.closest("form") || document.getElementById("agenda-ia-form");
        var input = form && (
            form.querySelector("textarea[name='note']")
            || form.querySelector("input[name='prompt']")
        );
        var homeReview = document.querySelector("[data-jrh-voice-review]");
        var isHome = Boolean(form && form.hasAttribute("data-jrh-interpret"));
        var overlay = document.getElementById("m-voice");
        var cancel = overlay && overlay.querySelector("[data-voice-cancel]");
        if (!window.JRH || !window.JRH.transcribeVoice) {
            return;
        }
        function setVoiceOpen(isOpen) {
            if (!overlay) {
                return;
            }
            overlay.hidden = !isOpen;
            document.body.classList.toggle("m-voice-open", isOpen);
            document.documentElement.classList.toggle("m-voice-open", isOpen);
        }
        var session = window.JRH.transcribeVoice({
            button: button,
            form: form,
            input: input,
            status: document.querySelector("[data-jrh-voice-status], [data-agenda-voice-status]"),
            unsupported: document.querySelector("[data-jrh-voice-unsupported]"),
            review: isHome ? homeReview : null,
            transcript: document.querySelector("[data-jrh-transcript]"),
            rerecord: document.querySelector("[data-jrh-rerecord]"),
            autoSubmit: !isHome,
            onState: function (name) {
                setVoiceOpen(name === "listening" || name === "transcribing");
            },
        });
        if (cancel && session && session.recognition) {
            cancel.addEventListener("click", function () {
                try {
                    session.recognition.stop();
                } catch (error) {
                    /* ignore */
                }
                setVoiceOpen(false);
            });
        }
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

    var sheet = document.getElementById("visit-close-sheet");
    if (sheet) {
        document.querySelectorAll("[data-visit-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                var form = sheet.querySelector("[data-visit-close-form]");
                var label = sheet.querySelector("[data-visit-close-label]");
                if (form) {
                    form.action = button.getAttribute("data-visit-close") || "";
                }
                if (label) {
                    label.textContent = button.getAttribute("data-visit-label") || "";
                }
                if (sheet.showModal) {
                    sheet.showModal();
                }
            });
        });
        var cancel = sheet.querySelector("[data-visit-close-cancel]");
        if (cancel) {
            cancel.addEventListener("click", function () {
                sheet.close();
            });
        }
    }
})();
