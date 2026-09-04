(function () {
    function attachVoice(button) {
        var Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
        var form = button.closest("form") || document.getElementById("agenda-ia-form");
        var input = form && (form.querySelector("textarea[name='note']") || form.querySelector("input[name='prompt']"));
        var status = document.querySelector("[data-agenda-voice-status]");

        if (!Speech || !input) {
            return;
        }

        var recognition = new Speech();
        recognition.lang = document.documentElement.lang || "es-AR";
        recognition.interimResults = false;

        button.addEventListener("click", function () {
            try {
                if (status) {
                    status.textContent = status.getAttribute("data-listening") || status.textContent;
                }
                recognition.start();
            } catch (error) {
                return;
            }
        });

        recognition.addEventListener("result", function (event) {
            var transcript = event.results[0][0].transcript;
            input.value = transcript;
            if (form && input.name === "prompt") {
                form.submit();
            }
        });
    }

    document.querySelectorAll("[data-agenda-voice]").forEach(attachVoice);

    if (document.querySelector("[data-agenda-autofocus-voice]")) {
        var auto = document.querySelector("[data-agenda-voice]");
        if (auto) {
            auto.click();
        }
    }
})();
