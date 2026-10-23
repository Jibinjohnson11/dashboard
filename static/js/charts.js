/* Dashboard charts (Chart.js, vendored locally). Data comes from a json_script block. */
(function () {
  "use strict";

  var COLORS = {
    draft: "#94a3b8",
    pending: "#f59e0b",
    approved: "#16a34a",
    needs_edit: "#f97316",
    rejected: "#dc2626",
    published: "#2563eb"
  };
  var GRID = "#eef2f7";
  var TEXT = "#64748b";

  function emptyNote(canvas, message) {
    var note = document.createElement("div");
    note.className = "chart-empty";
    note.textContent = message;
    canvas.parentElement.appendChild(note);
  }

  function sum(values) { return values.reduce(function (a, b) { return a + b; }, 0); }

  document.addEventListener("DOMContentLoaded", function () {
    var dataEl = document.getElementById("dashboard-chart-data");
    if (!dataEl || typeof window.Chart === "undefined") return;
    var data = JSON.parse(dataEl.textContent);
    var Chart = window.Chart;

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = TEXT;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.tooltip.backgroundColor = "#0f172a";
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;

    var statusCanvas = document.getElementById("chart-status");
    if (statusCanvas) {
      if (sum(data.status.values) === 0) {
        emptyNote(statusCanvas, "No content yet");
      } else {
        new Chart(statusCanvas, {
          type: "doughnut",
          data: {
            labels: data.status.labels,
            datasets: [{
              data: data.status.values,
              backgroundColor: data.status.keys.map(function (k) { return COLORS[k]; }),
              borderColor: "#fff",
              borderWidth: 2,
              hoverOffset: 6
            }]
          },
          options: {
            maintainAspectRatio: false,
            cutout: "68%",
            plugins: { legend: { position: "right" } }
          }
        });
      }
    }

    var dailyCanvas = document.getElementById("chart-daily");
    if (dailyCanvas) {
      new Chart(dailyCanvas, {
        type: "bar",
        data: {
          labels: data.daily.labels,
          datasets: [{ label: "Published", data: data.daily.values, backgroundColor: COLORS.published, borderRadius: 4, maxBarThickness: 18 }]
        },
        options: {
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
            y: { beginAtZero: true, grid: { color: GRID }, border: { display: false }, ticks: { precision: 0 } }
          }
        }
      });
      if (sum(data.daily.values) === 0) emptyNote(dailyCanvas, "Nothing published in the last 30 days");
    }

    var campaignCanvas = document.getElementById("chart-campaigns");
    if (campaignCanvas) {
      if (!data.campaigns.labels.length) {
        emptyNote(campaignCanvas, "No approved or published content yet");
      } else {
        new Chart(campaignCanvas, {
          type: "bar",
          data: {
            labels: data.campaigns.labels,
            datasets: [
              { label: "Approved", data: data.campaigns.approved, backgroundColor: COLORS.approved, borderRadius: 4, maxBarThickness: 22 },
              { label: "Published", data: data.campaigns.published, backgroundColor: COLORS.published, borderRadius: 4, maxBarThickness: 22 }
            ]
          },
          options: {
            maintainAspectRatio: false,
            indexAxis: "y",
            plugins: { legend: { position: "top", align: "end" } },
            scales: {
              x: { stacked: true, beginAtZero: true, grid: { color: GRID }, border: { display: false }, ticks: { precision: 0 } },
              y: {
                stacked: true,
                grid: { display: false },
                ticks: {
                  callback: function (value) {
                    var label = this.getLabelForValue(value);
                    var max = window.innerWidth < 600 ? 16 : 32;
                    return label.length > max ? label.slice(0, max - 1) + "…" : label;
                  }
                }
              }
            }
          }
        });
      }
    }
  });
})();
