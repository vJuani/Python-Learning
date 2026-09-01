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
    var grid = "rgba(15, 23, 42, 0.06)";

    function chartOptions(extra) {
        var options = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: muted, font: { size: 11 }, maxRotation: 0 },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: grid },
                    ticks: { color: muted, font: { size: 11 } },
                },
            },
        };

        if (extra) {
            Object.keys(extra).forEach(function (key) {
                options[key] = extra[key];
            });
        }

        return options;
    }

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
            options: chartOptions(),
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
                        pointRadius: 3,
                        pointBackgroundColor: accent,
                        pointBorderColor: "#fff",
                        pointBorderWidth: 2,
                    },
                    {
                        label: "previous",
                        data: previous,
                        borderColor: muted,
                        borderDash: [6, 6],
                        fill: false,
                        tension: 0.35,
                        pointRadius: 0,
                    },
                ],
            },
            options: chartOptions(),
        });
    }

    function areaChart(id, labels, values) {
        var canvas = document.getElementById(id);
        if (!canvas) {
            return;
        }

        var ctx = canvas.getContext("2d");
        var gradient = ctx.createLinearGradient(0, 0, 0, 200);
        gradient.addColorStop(0, "rgba(13, 71, 255, 0.22)");
        gradient.addColorStop(1, "rgba(13, 71, 255, 0)");

        new Chart(canvas, {
            type: "line",
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    borderColor: accent,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.35,
                    pointRadius: 3,
                    pointBackgroundColor: accent,
                    pointBorderColor: "#fff",
                    pointBorderWidth: 2,
                }],
            },
            options: chartOptions({
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: muted, font: { size: 11 }, maxRotation: 0 },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: grid },
                        ticks: {
                            color: muted,
                            font: { size: 11 },
                            callback: function (value) {
                                if (value >= 1000) {
                                    return "$" + Math.round(value / 1000) + "K";
                                }
                                return "$" + value;
                            },
                        },
                    },
                },
            }),
        });
    }

    function donutChart(id, items) {
        var canvas = document.getElementById(id);
        if (!canvas || !items || !items.length) {
            return;
        }

        var palette = {
            reservation: "#0d47ff",
            proposal: "#14b8a6",
            negotiation: "#8b5cf6",
            closing: "#f59e0b",
            rejected: "#22c55e",
            active: "#14b8a6",
            progress: "#22c55e",
            pending: "#f59e0b",
            cancelled: "#ef4444",
            closed: "#94a3b8",
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
                responsive: true,
                maintainAspectRatio: false,
                cutout: "72%",
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
                        pointRadius: 0,
                    },
                    {
                        label: "outflow",
                        data: cashFlow.outflow,
                        borderColor: "#ef4444",
                        tension: 0.35,
                        pointRadius: 0,
                    },
                    {
                        label: "net",
                        data: cashFlow.net,
                        borderColor: accent,
                        tension: 0.35,
                        pointRadius: 0,
                    },
                ],
            },
            options: chartOptions({
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: muted, font: { size: 11 }, maxRotation: 0 },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: grid },
                        ticks: {
                            color: muted,
                            font: { size: 11 },
                            callback: function (value) {
                                if (value >= 1000000) {
                                    return "$" + (value / 1000000).toFixed(1) + "M";
                                }
                                if (value >= 1000) {
                                    return "$" + Math.round(value / 1000) + "K";
                                }
                                return "$" + value;
                            },
                        },
                    },
                },
            }),
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

    var toolbar = document.querySelector(".reports-toolbar");
    if (toolbar) {
        toolbar.querySelectorAll("input, select").forEach(function (field) {
            field.addEventListener("change", function () {
                toolbar.submit();
            });
        });
    }
})();
