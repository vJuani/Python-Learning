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
        var reservationLine = form.querySelector("[data-visit-summary-reservation]");
        var propertyLine = form.querySelector("[data-visit-summary-property]");
        var agreedLine = form.querySelector("[data-visit-summary-agreed]");
        var stepLine = form.querySelector("[data-visit-summary-step]");
        var whenLine = form.querySelector("[data-visit-summary-when]");
        var reservation = form.querySelector("[data-visit-reservation]");
        var hideUnless = when ? (when.getAttribute("data-visit-show-unless") || "none") : "none";

        function fillLine(line, text) {
            if (!line) {
                return;
            }
            line.hidden = !text;
            var value = line.querySelector("[data-visit-summary-value]");
            if (value) {
                value.textContent = text || "";
            }
        }

        function moneyText(amountName, currencyName) {
            var amount = form.querySelector("[name='" + amountName + "']");
            var raw = amount && amount.value ? amount.value.trim() : "";
            if (!raw) {
                return "";
            }
            var currency = form.querySelector("input[name='" + currencyName + "']:checked");
            return (currency ? currency.value : "USD") + " " + raw;
        }

        var panels = {
            liked: ["note", "steps"],
            interested: ["note", "steps"],
            negotiate: ["note", "offer", "steps"],
            second_visit: ["note"],
            disliked: ["objections", "steps"],
            no_show: ["steps"],
            reserved: ["reservation", "steps"],
            other: ["note", "steps"]
        };
        var limitedSteps = {
            no_show: ["call", "whatsapp", "second_visit"]
        };

        function setFields(node, enabled) {
            if (!node) {
                return;
            }
            node.querySelectorAll("input, textarea, select").forEach(function (field) {
                field.disabled = !enabled;
            });
        }

        function showNode(node, enabled) {
            if (!node) {
                return;
            }
            node.hidden = !enabled;
            setFields(node, enabled);
        }

        function setEnabled(show) {
            [dateInput, timeInput, stepNote && stepNote.querySelector("input")].forEach(function (field) {
                if (field) {
                    field.disabled = !show;
                }
            });
        }

        function sync() {
            var resultInput = form.querySelector("input[name='result']:checked");
            var result = resultInput ? resultInput.value : "";
            var active = panels[result] || [];
            ["note", "objections", "offer", "steps"].forEach(function (name) {
                showNode(form.querySelector("[data-visit-panel='" + name + "']"), active.indexOf(name) >= 0);
            });
            var showReservation = active.indexOf("reservation") >= 0;
            showNode(reservation, showReservation);
            var purpose = form.getAttribute("data-listing-purpose") || "sale";
            var milestoneSet = purpose === "rental" || purpose === "temporary_rental" ? "rent" : "sale";
            form.querySelectorAll("[data-visit-milestones]").forEach(function (group) {
                var match = showReservation && group.getAttribute("data-visit-milestones") === milestoneSet;
                group.hidden = !match;
                setFields(group, match);
            });

            var allowedSteps = limitedSteps[result] || null;
            form.querySelectorAll("[data-visit-step]").forEach(function (label) {
                var key = label.getAttribute("data-visit-step");
                var visible = active.indexOf("steps") >= 0 && (!allowedSteps || allowedSteps.indexOf(key) >= 0);
                label.hidden = !visible;
                var input = label.querySelector("input");
                if (!input) {
                    return;
                }
                input.disabled = !visible;
                if (!visible) {
                    input.checked = false;
                }
            });

            if (result === "reserved" && !form.querySelector("input[name='next_step']:checked")) {
                var prepare = form.querySelector("input[name='next_step'][value='prepare_operation']");
                if (prepare && !prepare.disabled) {
                    prepare.checked = true;
                }
            }
            if (result === "negotiate" && !form.querySelector("input[name='next_step']:checked")) {
                var negotiate = form.querySelector("input[name='next_step'][value='negotiate']");
                if (negotiate && !negotiate.disabled) {
                    negotiate.checked = true;
                }
            }
            if (result === "second_visit") {
                var second = form.querySelector("input[name='next_step'][value='second_visit']");
                if (second) {
                    second.disabled = false;
                    second.checked = true;
                }
            }

            var stepInput = form.querySelector("input[name='next_step']:checked");
            var step = stepInput ? stepInput.value : "";
            var show = result === "second_visit" || (Boolean(step) && step !== hideUnless);
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

            fillLine(reservationLine, showReservation ? moneyText("reservation_amount", "reservation_currency") : "");
            if (propertyLine) {
                propertyLine.hidden = !showReservation;
            }
            fillLine(agreedLine, showReservation ? moneyText("agreed_property_price", "agreed_property_currency") : "");

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
        form.addEventListener("input", sync);
        form.addEventListener("reset", function () {
            window.setTimeout(sync, 0);
        });
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
