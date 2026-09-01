(function () {
    var dataNode = document.getElementById("reports-page-data");
    if (!dataNode || typeof Chart === "undefined") {
        return;
    }

    var panel;
    try {
        panel = JSON.parse(dataNode.textContent || "{}");
    } catch (error) {
        return;
    }

    var accent = "#0d47ff";
    var muted = "#94a3b8";
    var grid = "rgba(15, 23, 42, 0.08)";

    function barChart(id, labels, values) {
        var canvas = document.getElementById(id);
        if (!canvas) {
            return;
        }

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: accent,
                    borderRadius: 8,
                    maxBarThickness: 42,
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: muted } },
                    y: {
                        beginAtZero: true,
                        grid: { color: grid },
                        ticks: { color: muted, precision: 0 },
                    },
                },
            },
        });
    }

    function lineChart(id, labels, current, previous) {
        var canvas = document.getElementById(id);
        if (!canvas) {
            return;
        }

        new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "current",
                        data: current,
                        borderColor: accent,
                        backgroundColor: "rgba(13, 71, 255, 0.08)",
                        fill: false,
                        tension: 0.35,
                    },
                    {
                        label: "previous",
                        data: previous,
                        borderColor: muted,
                        borderDash: [6, 6],
                        fill: false,
                        tension: 0.35,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: muted } },
                    y: {
                        beginAtZero: true,
                        grid: { color: grid },
                        ticks: { color: muted, precision: 0 },
                    },
                },
            },
        });
    }

    function areaChart(id, labels, values) {
        var canvas = document.getElementById(id);
        if (!canvas) {
            return;
        }

        new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    borderColor: accent,
                    backgroundColor: "rgba(13, 71, 255, 0.15)",
                    fill: true,
                    tension: 0.35,
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: muted } },
                    y: {
                        beginAtZero: true,
                        grid: { color: grid },
                        ticks: { color: muted },
                    },
                },
            },
        });
    }

    function donutChart(id, items) {
        var canvas = document.getElementById(id);
        if (!canvas || !items || !items.length) {
            return;
        }

        var palette = {
            reservation: "#0d47ff",
            proposal: "#f59e0b",
            negotiation: "#8b5cf6",
            closing: "#22c55e",
            rejected: "#ef4444",
            active: "#0d47ff",
            progress: "#8b5cf6",
            pending: "#f59e0b",
            cancelled: "#ef4444",
            closed: "#22c55e",
            neutral: "#94a3b8",
        };

        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: items.map(function (item) { return item.label; }),
                datasets: [{
                    data: items.map(function (item) { return item.count; }),
                    backgroundColor: items.map(function (item) {
                        return palette[item.tone] || "#94a3b8";
                    }),
                    borderWidth: 0,
                }],
            },
            options: {
                cutout: "68%",
                plugins: { legend: { display: false } },
            },
        });
    }

    function cashflowChart(id, cashFlow) {
        var canvas = document.getElementById(id);
        if (!canvas || !cashFlow) {
            return;
        }

        new Chart(canvas, {
            type: "line",
            data: {
                labels: cashFlow.labels,
                datasets: [
                    {
                        label: "inflow",
                        data: cashFlow.inflow,
                        borderColor: "#22c55e",
                        tension: 0.35,
                    },
                    {
                        label: "outflow",
                        data: cashFlow.outflow,
                        borderColor: "#ef4444",
                        tension: 0.35,
                    },
                    {
                        label: "net",
                        data: cashFlow.net,
                        borderColor: accent,
                        tension: 0.35,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: true, position: "bottom" } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: muted } },
                    y: { grid: { color: grid }, ticks: { color: muted } },
                },
            },
        });
    }

    barChart(
        "reports-chart-weekly",
        (panel.weekly_operations || {}).labels || [],
        (panel.weekly_operations || {}).values || []
    );

    var evolution = panel.operations_evolution || {};
    lineChart(
        "reports-chart-evolution",
        evolution.labels || [],
        evolution.current || [],
        evolution.previous || []
    );

    donutChart("reports-chart-stages", panel.stage_distribution || []);
    donutChart("reports-chart-status", panel.status_distribution || []);

    var commission = panel.commission_trend || {};
    areaChart(
        "reports-chart-commissions",
        commission.labels || [],
        commission.values || []
    );

    cashflowChart("reports-chart-cashflow", panel.cash_flow);
})();
