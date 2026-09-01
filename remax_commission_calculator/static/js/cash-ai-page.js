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
    var chartHeight = 240;
    var ctx = canvas.getContext("2d");
    var fillGradient = ctx.createLinearGradient(0, 0, 0, chartHeight);
    fillGradient.addColorStop(0, "rgba(13, 71, 255, 0.22)");
    fillGradient.addColorStop(1, "rgba(13, 71, 255, 0)");

    function formatMoney(value) {
        var num = Number(value || 0);
        if (num >= 1000000) {
            return "$ " + (num / 1000000).toFixed(1).replace(".", ",") + "M";
        }
        if (num >= 1000) {
            return "$ " + Math.round(num / 1000) + "k";
        }
        return "$ " + Math.round(num);
    }

    new Chart(canvas, {
        type: "line",
        data: {
            labels: evolution.labels || [],
            datasets: [{
                label: "Saldo neto (ARS)",
                data: evolution.values || [],
                borderColor: "#0d47ff",
                backgroundColor: fillGradient,
                fill: true,
                tension: 0.35,
                pointRadius: 4,
                pointHoverRadius: 5,
                pointBackgroundColor: "#0d47ff",
                pointBorderColor: "#ffffff",
                pointBorderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            return formatMoney(ctx.parsed.y);
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: "#94a3b8", font: { size: 11 } },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: "rgba(15, 23, 42, 0.06)" },
                    ticks: {
                        color: "#94a3b8",
                        font: { size: 11 },
                        callback: function (value) {
                            return formatMoney(value);
                        },
                    },
                },
            },
        },
    });
})();
