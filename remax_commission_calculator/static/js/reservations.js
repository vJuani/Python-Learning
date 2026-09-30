(function () {
    function setHidden(node, hidden) {
        if (!node) {
            return;
        }
        node.hidden = hidden;
    }

    function backdrop() {
        return document.querySelector("[data-res-backdrop]");
    }

    document.addEventListener("click", function (event) {
        var tab = event.target.closest("[data-res-tab]");
        if (tab) {
            var root = tab.closest("[data-res-switch]");
            if (!root) {
                return;
            }
            var name = tab.getAttribute("data-res-tab");
            root.querySelectorAll("[data-res-tab]").forEach(function (button) {
                button.setAttribute("aria-selected", button === tab ? "true" : "false");
            });
            root.querySelectorAll("[data-res-pane]").forEach(function (pane) {
                pane.hidden = pane.getAttribute("data-res-pane") !== name;
            });
            return;
        }
        var formButton = event.target.closest("[data-res-form]");
        if (formButton) {
            var host = formButton.closest(".res-detail") || document;
            var sheetName = formButton.getAttribute("data-res-form");
            host.querySelectorAll("[data-res-sheet]").forEach(function (sheet) {
                var selected = sheet.getAttribute("data-res-sheet") === sheetName;
                sheet.hidden = selected ? !sheet.hidden : true;
            });
            setHidden(host.querySelector("[data-res-more-menu]"), true);
            return;
        }
        var more = event.target.closest("[data-res-more]");
        if (more) {
            var moreMenu = more.parentElement.querySelector("[data-res-more-menu]");
            if (moreMenu) {
                moreMenu.hidden = !moreMenu.hidden;
            }
        }
    });

    var board = document.querySelector("[data-res-board]");
    if (!board) {
        return;
    }

    var drawer = document.querySelector("[data-res-drawer]");
    var panel = document.querySelector("[data-res-panel]");
    var openFilters = document.querySelector("[data-res-filters-open]");
    var closeFilters = document.querySelector("[data-res-filters-close]");

    if (openFilters) {
        openFilters.addEventListener("click", function () {
            setHidden(drawer, false);
            setHidden(backdrop(), false);
        });
    }
    if (closeFilters) {
        closeFilters.addEventListener("click", function () {
            setHidden(drawer, true);
            if (!panel || panel.hidden) {
                setHidden(backdrop(), true);
            }
        });
    }

    var countNode = document.querySelector("[data-res-filter-count]");
    var hintNode = document.querySelector("[data-res-filter-hint]");
    var summary = document.querySelector("[data-res-filter-summary]");

    function paintFilters() {
        if (!drawer || !countNode) {
            return;
        }
        var total = 0;
        drawer.querySelectorAll("select, input").forEach(function (field) {
            if (field.type === "hidden") {
                return;
            }
            var empty = !(field.value || "").trim();
            field.classList.toggle("is-empty", field.tagName === "SELECT" && empty);
            if (!empty) {
                total += 1;
            }
        });
        var label = countNode.getAttribute("data-zero");
        if (total === 1) {
            label = countNode.getAttribute("data-one");
        } else if (total > 1) {
            label = (countNode.getAttribute("data-many") || "").replace("{n}", String(total));
        }
        countNode.textContent = label;
        if (hintNode) {
            hintNode.textContent = total ? hintNode.getAttribute("data-some") : hintNode.getAttribute("data-all");
        }
        if (summary) {
            summary.classList.toggle("is-active", total > 0);
        }
    }

    if (drawer) {
        drawer.addEventListener("input", paintFilters);
        drawer.addEventListener("change", paintFilters);
        paintFilters();
    }

    function fold(value) {
        return (value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }

    var agentInput = document.querySelector("[data-res-agent-input]");
    var agentId = document.querySelector("[data-res-agent-id]");
    var agentList = document.querySelector("[data-res-agent-list]");
    var agentData = document.getElementById("res-agents");
    var agents = [];
    if (agentData) {
        try {
            agents = JSON.parse(agentData.textContent || "[]");
        } catch (error) {
            agents = [];
        }
    }

    var agentTimer = 0;
    function renderAgents() {
        if (!agentInput || !agentList) {
            return;
        }
        var query = fold(agentInput.value);
        agentList.innerHTML = "";
        if (!query) {
            agentList.hidden = true;
            return;
        }
        var matches = agents.filter(function (agent) {
            return fold(agent.name).indexOf(query) !== -1;
        }).slice(0, 8);
        matches.forEach(function (agent) {
            var item = document.createElement("li");
            var button = document.createElement("button");
            button.type = "button";
            button.textContent = agent.name;
            button.addEventListener("click", function () {
                agentInput.value = agent.name;
                if (agentId) {
                    agentId.value = String(agent.id);
                }
                agentList.hidden = true;
                paintFilters();
            });
            item.appendChild(button);
            agentList.appendChild(item);
        });
        agentList.hidden = matches.length === 0;
    }

    if (agentInput) {
        agentInput.addEventListener("input", function () {
            if (agentId) {
                agentId.value = "";
            }
            window.clearTimeout(agentTimer);
            agentTimer = window.setTimeout(renderAgents, 250);
        });
    }

    function openReservation(id) {
        if (!panel || !window.matchMedia("(min-width: 961px)").matches) {
            window.location.href = "/reservations/" + id;
            return;
        }
        panel.hidden = false;
        setHidden(backdrop(), false);
        panel.setAttribute("aria-busy", "true");
        fetch("/reservations/" + id + "?panel=1", { credentials: "same-origin" })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("panel");
                }
                return response.text();
            })
            .then(function (html) {
                panel.innerHTML = html;
                panel.removeAttribute("aria-busy");
            })
            .catch(function () {
                window.location.href = "/reservations/" + id;
            });
    }

    board.addEventListener("click", function (event) {
        var opener = event.target.closest("[data-res-open]");
        if (opener) {
            event.preventDefault();
            openReservation(opener.getAttribute("data-res-open"));
            return;
        }
        var row = event.target.closest("[data-res-row]");
        if (!row || event.target.closest("a, button, input, select, textarea")) {
            return;
        }
        openReservation(row.getAttribute("data-res-row"));
    });

    function closePanel() {
        if (!panel) {
            return;
        }
        panel.hidden = true;
        panel.innerHTML = "";
        setHidden(backdrop(), true);
    }

    if (panel) {
        panel.addEventListener("click", function (event) {
            if (event.target.closest("[data-res-close]")) {
                closePanel();
            }
        });
    }

    document.addEventListener("click", function (event) {
        if (event.target.closest("[data-res-backdrop]")) {
            closePanel();
            setHidden(drawer, true);
        }
    });
}());
