// The Fragile — dashboard frontend.
// Fetches readings for the selected range, draws the pressure chart, and fills the
// "current reading" cards (highlighting the delta cards when a drop threshold is breached).

(function () {
  const body = document.body;
  const T1 = parseFloat(body.dataset.threshold1h);
  const T3 = parseFloat(body.dataset.threshold3h);

  let chart = null;
  let activeRange = "24h";

  const fmt = (v, digits, sign) =>
    v === null || v === undefined ? "—" : (sign ? (v >= 0 ? "+" : "") : "") + v.toFixed(digits);

  async function fetchReadings(params) {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch("/api/readings?" + qs);
    if (!res.ok) throw new Error("fetch failed: " + res.status);
    return res.json();
  }

  function drawChart(rows) {
    const labels = rows.map((r) => new Date(r.ts).toLocaleString());
    const data = rows.map((r) => r.pressure_hpa);
    const cfg = {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Pressure (hPa)",
          data,
          borderColor: "#6ea8fe",
          backgroundColor: "rgba(110,168,254,0.12)",
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { maxTicksLimit: 8, color: "#8b93a1" }, grid: { color: "#2a2f38" } },
          y: { ticks: { color: "#8b93a1" }, grid: { color: "#2a2f38" } },
        },
        plugins: { legend: { labels: { color: "#e7e9ee" } } },
      },
    };
    if (chart) {
      chart.data = cfg.data;
      chart.update();
    } else {
      chart = new Chart(document.getElementById("chart"), cfg);
    }
  }

  function updateCards(rows) {
    if (!rows.length) return;
    const last = rows[rows.length - 1];
    document.getElementById("c-pressure").textContent = fmt(last.pressure_hpa, 2);
    document.getElementById("c-humidity").textContent = fmt(last.humidity_pct, 1);
    document.getElementById("c-temp").textContent = fmt(last.temp_c, 2);
    document.getElementById("c-d1h").textContent = fmt(last.pressure_change_1h, 2, true);
    document.getElementById("c-d3h").textContent = fmt(last.pressure_change_3h, 2, true);
    document.getElementById("c-ts").textContent = new Date(last.ts).toLocaleString();

    toggleAlert("card-d1h", last.pressure_change_1h, T1);
    toggleAlert("card-d3h", last.pressure_change_3h, T3);
  }

  function toggleAlert(id, delta, threshold) {
    const el = document.getElementById(id);
    const breached = delta !== null && delta !== undefined && delta <= -threshold;
    el.classList.toggle("alert", breached);
  }

  async function load(params) {
    try {
      const rows = await fetchReadings(params);
      drawChart(rows);
      updateCards(rows);
    } catch (e) {
      console.error(e);
    }
  }

  // Preset buttons
  document.querySelectorAll(".preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeRange = btn.dataset.range;
      document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("active", b === btn));
      load({ range: activeRange });
    });
  });

  // Custom from/to range
  document.getElementById("custom-range").addEventListener("submit", (e) => {
    e.preventDefault();
    const from = document.getElementById("from").value;
    const to = document.getElementById("to").value;
    if (!from && !to) return;
    document.querySelectorAll(".preset").forEach((b) => b.classList.remove("active"));
    const params = {};
    if (from) params.from = new Date(from).toISOString();
    if (to) params.to = new Date(to).toISOString();
    load(params);
  });

  // Initial load + light auto-refresh on preset ranges.
  document.querySelector('.preset[data-range="24h"]').classList.add("active");
  load({ range: activeRange });
  setInterval(() => {
    if (activeRange) load({ range: activeRange });
  }, 60000);
})();
