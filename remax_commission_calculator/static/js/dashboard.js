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

    document.addEventListener("DOMContentLoaded", function () {
        if (!window.__dashboardChartMode) {
            return;
        }

        if (window.__chartJsFailed || typeof Chart === "undefined") {
            showChartsFallback();
            return;
        }

        var data = window.__reportsChartData || {};
        var payload = data.invoiced_split;
        var canvas = document.getElementById("chart-invoiced-split");

        if (!canvas || !payload) {
            return;
        }

        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: payload.labels || [],
                datasets: [
                    {
                        data: payload.values || [],
                        backgroundColor: ["#22c55e", "#f59e0b"],
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
    });
})();
