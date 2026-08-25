(function () {
    function showChartsFallback() {
        var fallback = document.getElementById("reports-charts-fallback");
        var grid = document.getElementById("reports-charts-grid");

        if (fallback) {
            fallback.hidden = false;
        }

        if (grid) {
            grid.hidden = true;
        }
    }

    function makeChart(canvasId, type, payload, options) {
        var canvas = document.getElementById(canvasId);

        if (!canvas || !payload) {
            return;
        }

        return new Chart(canvas, {
            type: type,
            data: {
                labels: payload.labels || [],
                datasets: [
                    {
                        label: payload.title || "",
                        data: payload.values || [],
                        backgroundColor: options.backgroundColor,
                        borderColor: options.borderColor || "#0860C8",
                        borderWidth: options.borderWidth || 1,
                        tension: 0.25,
                        fill: options.fill || false
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        display: type === "doughnut"
                    }
                },
                scales: type === "doughnut"
                    ? {}
                    : {
                        y: {
                            beginAtZero: true
                        }
                    }
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (window.__chartJsFailed || typeof Chart === "undefined") {
            showChartsFallback();
            return;
        }

        var data = window.__reportsChartData || {};
        var accent = "#0860C8";
        var soft = "rgba(8, 96, 200, 0.35)";
        var statusColors = [
            "#94a3b8",
            "#f59e0b",
            "#22c55e",
            "#ef4444"
        ];

        makeChart(
            "chart-commissions-month",
            "line",
            data.commissions_by_month,
            {
                backgroundColor: soft,
                borderColor: accent,
                fill: true
            }
        );
        makeChart(
            "chart-operations-month",
            "bar",
            data.operations_by_month,
            {
                backgroundColor: soft,
                borderColor: accent
            }
        );
        makeChart(
            "chart-agent-ranking",
            "bar",
            data.agent_ranking,
            {
                backgroundColor: soft,
                borderColor: accent
            }
        );

        var statusCanvas = document.getElementById("chart-status");
        var statusPayload = data.status_distribution;

        if (statusCanvas && statusPayload) {
            new Chart(statusCanvas, {
                type: "doughnut",
                data: {
                    labels: statusPayload.labels || [],
                    datasets: [
                        {
                            data: statusPayload.values || [],
                            backgroundColor: statusColors,
                            borderWidth: 1,
                            borderColor: "#ffffff"
                        }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: "bottom"
                        }
                    }
                }
            });
        }
    });
})();
