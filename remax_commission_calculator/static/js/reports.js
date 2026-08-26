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

    function hideChartsFallback() {
        var fallback = document.getElementById("reports-charts-fallback");

        if (fallback) {
            fallback.hidden = true;
        }
    }

    function readCssVar(name, fallback) {
        var value = getComputedStyle(document.documentElement)
            .getPropertyValue(name)
            .trim();

        return value || fallback;
    }

    function chartTheme() {
        var isDark =
            document.documentElement.getAttribute("data-theme") === "dark";
        var muted = readCssVar("--text-muted", isDark ? "#93a0b8" : "#5b6b7c");
        var grid = isDark
            ? "rgba(148, 163, 184, 0.12)"
            : "rgba(15, 23, 42, 0.08)";
        var accent = readCssVar("--accent", "#0860C8");

        return {
            isDark: isDark,
            muted: muted,
            grid: grid,
            accent: accent,
            soft: isDark
                ? "rgba(59, 130, 246, 0.28)"
                : "rgba(8, 96, 200, 0.22)",
            tooltipBg: isDark ? "#151f33" : "#ffffff",
            tooltipText: isDark ? "#e8eefc" : "#0f172a",
            border: isDark ? "#0b1426" : "#ffffff"
        };
    }

    function basePlugins(theme) {
        return {
            legend: {
                display: false
            },
            tooltip: {
                backgroundColor: theme.tooltipBg,
                titleColor: theme.tooltipText,
                bodyColor: theme.tooltipText,
                borderColor: theme.grid,
                borderWidth: 1,
                padding: 10,
                displayColors: false
            }
        };
    }

    function baseScales(theme) {
        return {
            x: {
                grid: {
                    color: theme.grid,
                    drawBorder: false
                },
                ticks: {
                    color: theme.muted,
                    font: { size: 11 }
                },
                border: { display: false }
            },
            y: {
                beginAtZero: true,
                grid: {
                    color: theme.grid,
                    drawBorder: false
                },
                ticks: {
                    color: theme.muted,
                    font: { size: 11 }
                },
                border: { display: false }
            }
        };
    }

    function makeChart(canvasId, type, payload, options, theme) {
        var canvas = document.getElementById(canvasId);

        if (!canvas || !payload) {
            return null;
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
                        borderColor: options.borderColor || theme.accent,
                        borderWidth: options.borderWidth || 2,
                        tension: 0.3,
                        fill: options.fill || false,
                        pointRadius: type === "line" ? 3 : 0,
                        pointHoverRadius: type === "line" ? 5 : 0,
                        borderRadius: type === "bar" ? 6 : 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: basePlugins(theme),
                scales: type === "doughnut" ? {} : baseScales(theme)
            }
        });
    }

    function renderStatusLegend(payload, colors) {
        var legend = document.getElementById("chart-status-legend");

        if (!legend || !payload) {
            return;
        }

        var labels = payload.labels || [];
        var values = payload.values || [];
        var total = values.reduce(function (sum, value) {
            return sum + (Number(value) || 0);
        }, 0);

        var items = [];

        for (var index = 0; index < labels.length; index += 1) {
            var count = Number(values[index]) || 0;

            if (count <= 0 && total > 0) {
                continue;
            }

            var percent = total
                ? Math.round((count / total) * 1000) / 10
                : 0;
            var color = colors[index % colors.length];

            items.push(
                "<li>" +
                    '<span class="legend-label">' +
                    '<span class="legend-swatch" style="background:' +
                    color +
                    '"></span>' +
                    labels[index] +
                    "</span>" +
                    '<span class="legend-value">' +
                    count +
                    " · " +
                    percent +
                    "%</span>" +
                    "</li>"
            );
        }

        legend.innerHTML = items.join("");
    }

    document.addEventListener("DOMContentLoaded", function () {
        hideChartsFallback();

        if (window.__chartJsFailed || typeof Chart === "undefined") {
            showChartsFallback();
            return;
        }

        var data = window.__reportsChartData || {};
        var theme = chartTheme();
        var statusColors = ["#94a3b8", "#f59e0b", "#22c55e", "#ef4444"];

        try {
            makeChart(
                "chart-commissions-month",
                "line",
                data.commissions_by_month,
                {
                    backgroundColor: theme.soft,
                    borderColor: theme.accent,
                    fill: true
                },
                theme
            );
            makeChart(
                "chart-operations-month",
                "bar",
                data.operations_by_month,
                {
                    backgroundColor: theme.soft,
                    borderColor: theme.accent
                },
                theme
            );
            makeChart(
                "chart-agent-ranking",
                "bar",
                data.agent_ranking,
                {
                    backgroundColor: theme.soft,
                    borderColor: theme.accent
                },
                theme
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
                                borderWidth: 2,
                                borderColor: theme.border,
                                hoverOffset: 4
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        cutout: "68%",
                        plugins: {
                            legend: { display: false },
                            tooltip: basePlugins(theme).tooltip
                        }
                    }
                });
                renderStatusLegend(statusPayload, statusColors);
            }

            hideChartsFallback();
        } catch (error) {
            showChartsFallback();
        }
    });
})();
