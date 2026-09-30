(function () {
    function pad(value) {
        return String(value).padStart(2, "0");
    }

    function isoDaysFromToday(days) {
        var date = new Date();
        date.setDate(date.getDate() + days);
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
    }

    function selectedLabel(form, name) {
        var input = form.querySelector("input[name='" + name + "']:checked");
        if (!input) {
            return "";
        }
        var text = input.closest("label");
        var span = text && text.querySelector("[data-visit-label-text]");
        return span ? span.textContent.trim() : "";
    }

    function bind(root) {
        var form = root.matches("form") ? root : root.querySelector("form");
        if (!form || form.getAttribute("data-visit-close-bound") === "1") {
            return;
        }
        form.setAttribute("data-visit-close-bound", "1");

        var when = form.querySelector("[data-visit-when]");
        var stepNote = form.querySelector("[data-visit-step-note]");
        var dateInput = form.querySelector("[name='next_step_date']");
        var timeInput = form.querySelector("[name='next_step_time']");
        var dateField = form.querySelector("[data-visit-date-field]");
        var resultLine = form.querySelector("[data-visit-summary-result]");
        var stepLine = form.querySelector("[data-visit-summary-step]");
        var whenLine = form.querySelector("[data-visit-summary-when]");
        var hideUnless = when ? (when.getAttribute("data-visit-show-unless") || "none") : "none";

        function setEnabled(show) {
            [dateInput, timeInput, stepNote && stepNote.querySelector("input")].forEach(function (field) {
                if (field) {
                    field.disabled = !show;
                }
            });
        }

        function sync() {
            var stepInput = form.querySelector("input[name='next_step']:checked");
            var step = stepInput ? stepInput.value : "";
            var show = Boolean(step) && step !== hideUnless;
            if (when) {
                when.hidden = !show;
            }
            if (stepNote) {
                stepNote.hidden = !show;
            }
            setEnabled(show);

            var resultText = selectedLabel(form, "result");
            if (resultLine) {
                resultLine.hidden = !resultText;
                var resultValue = resultLine.querySelector("[data-visit-summary-value]");
                if (resultValue) {
                    resultValue.textContent = resultText;
                }
            }

            var stepText = selectedLabel(form, "next_step");
            if (stepLine) {
                stepLine.hidden = !stepText;
                var stepValue = stepLine.querySelector("[data-visit-summary-value]");
                if (stepValue) {
                    stepValue.textContent = stepText;
                }
            }

            if (whenLine) {
                var dateValue = show && dateInput ? dateInput.value : "";
                var timeValue = show && timeInput ? timeInput.value : "";
                whenLine.hidden = !dateValue;
                var whenValue = whenLine.querySelector("[data-visit-summary-value]");
                if (whenValue && dateValue) {
                    var parts = dateValue.split("-");
                    var parsed = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
                    var formatted = parsed.toLocaleDateString(document.documentElement.lang || "es", {
                        day: "numeric",
                        month: "long"
                    });
                    whenValue.textContent = timeValue ? formatted + " · " + timeValue : formatted;
                }
            }
        }

        form.addEventListener("change", sync);
        form.querySelectorAll("[data-visit-offset]").forEach(function (button) {
            button.addEventListener("click", function () {
                if (!dateInput) {
                    return;
                }
                dateInput.disabled = false;
                dateInput.value = isoDaysFromToday(Number(button.getAttribute("data-visit-offset")) || 0);
                if (dateField) {
                    dateField.hidden = true;
                }
                form.querySelectorAll("[data-visit-offset], [data-visit-pick]").forEach(function (item) {
                    item.classList.toggle("is-selected", item === button);
                    item.setAttribute("aria-pressed", item === button ? "true" : "false");
                });
                sync();
            });
        });

        var pick = form.querySelector("[data-visit-pick]");
        if (pick) {
            pick.addEventListener("click", function () {
                if (dateField) {
                    dateField.hidden = false;
                }
                if (dateInput) {
                    dateInput.disabled = false;
                    dateInput.focus();
                }
                form.querySelectorAll("[data-visit-offset]").forEach(function (item) {
                    item.classList.remove("is-selected");
                    item.setAttribute("aria-pressed", "false");
                });
                pick.classList.add("is-selected");
                pick.setAttribute("aria-pressed", "true");
            });
        }

        sync();
    }

    document.querySelectorAll(".visit-close").forEach(bind);
})();
