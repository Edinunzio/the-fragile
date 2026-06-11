// The Fragile — dashboard frontend.
// Fetches readings for the selected range, draws the pressure chart, and fills the
// "current reading" cards (highlighting the delta cards when a drop threshold is breached).
//
// Pressure is STORED and alert-checked in hPa; the UI can display hPa or inHg. Conversion is
// linear (inHg = hPa / 33.8639), so deltas convert with the same factor — only the displayed
// numbers change, never the alert logic.

(function () {
  const body = document.body;
  const T1 = parseFloat(body.dataset.threshold1h); // hPa
  const T3 = parseFloat(body.dataset.threshold3h); // hPa
  const HPA_PER_INHG = 33.8639;

  let chart = null;
  let activeRange = "24h";
  let unit = "inHg";
  let tempUnit = "F";
  let lastSeries = [];
  let lastLatest = null;

  // hPa -> current display unit.
  const conv = (hpa) => (hpa == null ? null : unit === "inHg" ? hpa / HPA_PER_INHG : hpa);
  // stored °C -> current display unit.
  const convT = (c) => (c == null ? null : tempUnit === "F" ? c * 9 / 5 + 32 : c);
  const pDigits = () => (unit === "inHg" ? 2 : 1); // absolute pressure
  const dDigits = () => (unit === "inHg" ? 3 : 1); // deltas (inHg deltas are small)

  const fmt = (v, digits, sign) =>
    v == null ? "—" : (sign && v >= 0 ? "+" : "") + v.toFixed(digits);

  async function fetchReadings(params) {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch("/api/readings?" + qs);
    if (!res.ok) throw new Error("fetch failed: " + res.status);
    return res.json();
  }

  function drawChart(rows) {
    const labels = rows.map((r) => new Date(r.ts).toLocaleString());
    // Downsampled ranges carry per-bucket min/max; draw that as a faint band behind the
    // average line. Raw ranges have min==max==pressure, so the band is invisible.
    const datasets = [
      { label: "_max", data: rows.map((r) => conv(r.pressure_max)), borderWidth: 0, pointRadius: 0, fill: false },
      {
        label: "_min",
        data: rows.map((r) => conv(r.pressure_min)),
        borderWidth: 0,
        pointRadius: 0,
        backgroundColor: "rgba(110,168,254,0.10)",
        fill: "-1",
      },
      {
        label: "Pressure (" + unit + ")",
        data: rows.map((r) => conv(r.pressure_hpa)),
        borderColor: "#6ea8fe",
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: "#6ea8fe",
        tension: 0.2,
      },
    ];
    const cfg = {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Trigger hover/tooltip anywhere along the x-position, not only when the cursor is
        // exactly on an (invisible) point.
        interaction: { mode: "index", intersect: false },
        onHover: (event, elements) => {
          showPoint(elements && elements.length ? elements[0].index : null);
        },
        scales: {
          x: { ticks: { maxTicksLimit: 8, color: "#8b93a1" }, grid: { color: "#2a2f38" } },
          y: { ticks: { color: "#8b93a1" }, grid: { color: "#2a2f38" } },
        },
        plugins: {
          legend: { labels: { color: "#e7e9ee", filter: (item) => !item.text.startsWith("_") } },
          // Hide the internal band datasets from the tooltip too.
          tooltip: { filter: (item) => !item.dataset.label.startsWith("_") },
        },
      },
    };
    if (chart) {
      chart.data = cfg.data;
      chart.update();
    } else {
      chart = new Chart(document.getElementById("chart"), cfg);
    }
  }

  function updateCards(last) {
    if (!last) return;
    document.getElementById("c-pressure").textContent = fmt(conv(last.pressure_hpa), pDigits());
    document.getElementById("c-humidity").textContent = fmt(last.humidity_pct, 1);
    document.getElementById("c-temp").textContent = fmt(convT(last.temp_c), 1);
    document.getElementById("c-d1h").textContent = fmt(conv(last.pressure_change_1h), dDigits(), true);
    document.getElementById("c-d3h").textContent = fmt(conv(last.pressure_change_3h), dDigits(), true);
    document.getElementById("c-ts").textContent = new Date(last.ts).toLocaleString();

    // Alert check uses raw hPa values + hPa thresholds, regardless of display unit.
    toggleAlert("card-d1h", last.pressure_change_1h, T1);
    toggleAlert("card-d3h", last.pressure_change_3h, T3);

    document.querySelectorAll(".punit").forEach((el) => (el.textContent = unit));
    document.querySelectorAll(".tunit").forEach((el) => (el.textContent = tempUnit === "F" ? "°F" : "°C"));
  }

  function toggleAlert(id, deltaHpa, thresholdHpa) {
    const breached = deltaHpa != null && deltaHpa <= -thresholdHpa;
    document.getElementById(id).classList.toggle("alert", breached);
  }

  // Point the cards at a hovered chart point (scrubbing), or back to the latest reading.
  function showPoint(i) {
    const scrubbing = i != null && !!lastSeries[i];
    document.body.classList.toggle("scrubbing", scrubbing);
    document.getElementById("ts-label").textContent = scrubbing ? "At cursor" : "As of";
    updateCards(scrubbing ? lastSeries[i] : lastLatest);
  }

  function render() {
    drawChart(lastSeries);
    updateCards(lastLatest);
  }

  async function load(params) {
    try {
      // Chart series (possibly downsampled) and the true latest reading for the cards are
      // fetched independently — the cards must show the real last reading and its real
      // deltas, not a downsampled bucket average.
      const [series, latest] = await Promise.all([
        fetchReadings(params),
        fetch("/api/latest").then((r) => r.json()),
      ]);
      lastSeries = series;
      lastLatest = latest;
      render();
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

  // Unit toggle — re-renders from cached data, no refetch.
  document.querySelectorAll(".unit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      unit = btn.dataset.unit;
      document.querySelectorAll(".unit-btn").forEach((b) => b.classList.toggle("active", b === btn));
      render();
    });
  });

  // Temperature unit toggle (°F default).
  document.querySelectorAll(".tunit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      tempUnit = btn.dataset.tunit;
      document.querySelectorAll(".tunit-btn").forEach((b) => b.classList.toggle("active", b === btn));
      render();
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

  // "Update" button — pull recent NOAA data, then refresh the view.
  const syncBtn = document.getElementById("sync-btn");
  const syncStatus = document.getElementById("sync-status");
  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncStatus.textContent = "Syncing…";
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      syncStatus.textContent = data.inserted > 0 ? `Added ${data.inserted} readings` : "Up to date";
      await load(activeRange ? { range: activeRange } : { range: "24h" });
    } catch (e) {
      syncStatus.textContent = "Sync failed";
      console.error(e);
    } finally {
      syncBtn.disabled = false;
      setTimeout(() => (syncStatus.textContent = ""), 4000);
    }
  });

  // Leaving the chart returns the cards to the latest reading.
  document.getElementById("chart").addEventListener("mouseleave", () => showPoint(null));

  // Initial load + light auto-refresh on preset ranges.
  document.querySelector('.preset[data-range="24h"]').classList.add("active");
  load({ range: activeRange });
  setInterval(() => {
    if (activeRange) load({ range: activeRange });
  }, 60000);
})();
