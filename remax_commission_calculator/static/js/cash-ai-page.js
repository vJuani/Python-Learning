(function () {
    var dataNode = document.getElementById("cash-ai-panel-data");
    if (!dataNode || typeof Chart === "undefined") {
        return;
    }

    var panel;
    try {
        panel = JSON.parse(dataNode.textContent || "{}");
    } catch (error) {
        return;
    }

    var canvas = document.getElementById("cash-ai-evolution-chart");
    if (!canvas) {
        return;
    }

    var evolution = panel.evolution || {};

    new Chart(canvas, {
        type: "line",
        data: {
            labels: evolution.labels || [],
            datasets: [{
                data: evolution.values || [],
                borderColor: "#0d47ff",
                backgroundColor: "rgba(13, 71, 255, 0.12)",
                fill: true,
                tension: 0.35,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: "#94a3b8" },
                },
                y: {
                    grid: { color: "rgba(15, 23, 42, 0.08)" },
                    ticks: { color: "#94a3b8" },
                },
            },
        },
    });
})();
