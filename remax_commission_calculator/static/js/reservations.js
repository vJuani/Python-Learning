(function () {
    var board = document.querySelector("[data-res-board]");
    if (!board) {
        return;
    }

    var drawer = document.querySelector("[data-res-drawer]");
    var panel = document.querySelector("[data-res-panel]");
    var openFilters = document.querySelector("[data-res-filters-open]");
    var closeFilters = document.querySelector("[data-res-filters-close]");

    function setHidden(node, hidden) {
        if (!node) {
            return;
        }
        node.hidden = hidden;
    }

    if (openFilters) {
        openFilters.addEventListener("click", function () {
            setHidden(drawer, false);
        });
    }
    if (closeFilters) {
        closeFilters.addEventListener("click", function () {
            setHidden(drawer, true);
        });
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

    if (panel) {
        panel.addEventListener("click", function (event) {
            if (event.target.closest("[data-res-close]")) {
                panel.hidden = true;
                panel.innerHTML = "";
            }
        });
    }
}());
