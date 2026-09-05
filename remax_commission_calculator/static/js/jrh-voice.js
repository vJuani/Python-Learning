(function (root) {
    var JRH = root.JRH || {};

    JRH.supportsSpeech = function () {
        return Boolean(root.SpeechRecognition || root.webkitSpeechRecognition);
    };

    JRH.transcribeVoice = function (options) {
        options = options || {};
        var Speech = root.SpeechRecognition || root.webkitSpeechRecognition;
        var button = options.button;
        var input = options.input;
        var status = options.status;
        var unsupported = options.unsupported;
        var review = options.review;
        var transcriptEl = options.transcript;
        var onTranscript = options.onTranscript;
        var autoSubmit = Boolean(options.autoSubmit);
        var form = options.form;

        function setState(name, text) {
            if (status) {
                if (text) {
                    status.textContent = text;
                    status.hidden = false;
                } else if (name === "idle") {
                    status.hidden = true;
                }
            }
            if (typeof options.onState === "function") {
                options.onState(name, text || "");
            }
        }

        if (!Speech) {
            if (unsupported) {
                unsupported.hidden = false;
            }
            if (button) {
                button.hidden = true;
            }
            return null;
        }

        var recognition = new Speech();
        recognition.lang = document.documentElement.lang || "es-AR";
        recognition.interimResults = false;

        function start() {
            try {
                setState(
                    "listening",
                    (status && status.getAttribute("data-listening")) || ""
                );
                if (button) {
                    button.classList.add("is-listening");
                }
                recognition.start();
            } catch (error) {
                setState("idle");
            }
        }

        if (button) {
            button.addEventListener("click", start);
        }
        if (options.rerecord) {
            options.rerecord.addEventListener("click", function () {
                if (review) {
                    review.hidden = true;
                }
                start();
            });
        }

        recognition.addEventListener("speechend", function () {
            setState(
                "transcribing",
                (status && status.getAttribute("data-transcribing")) || ""
            );
        });

        recognition.addEventListener("end", function () {
            if (button) {
                button.classList.remove("is-listening");
            }
        });

        recognition.addEventListener("result", function (event) {
            var transcript = event.results[0][0].transcript || "";
            setState(
                "heard",
                (status && status.getAttribute("data-heard")) || ""
            );
            if (input) {
                input.value = transcript;
            }
            if (transcriptEl) {
                transcriptEl.textContent = transcript;
            }
            if (review) {
                review.hidden = false;
            }
            if (typeof onTranscript === "function") {
                onTranscript(transcript);
            }
            if (autoSubmit && form && transcript) {
                form.submit();
            }
        });

        return {
            start: start,
            recognition: recognition,
        };
    };

    root.JRH = JRH;
})(window);
