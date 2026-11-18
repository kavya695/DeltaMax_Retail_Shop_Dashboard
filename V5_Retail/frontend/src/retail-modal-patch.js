const fieldMap = [
  ["Credit score", "Purchases", value => value.match(/(\d+) purchase/)?.[1] ?? "—"],
  ["Creditworthiness", "Total value", value => value.match(/Total value ([\d.,]+)/)?.[1] ?? "—"],
  ["Payment history", "Days inactive", value => value.match(/(\d+) days since last activity/)?.[1] ?? "—"],
  ["Delinquent accounts", "Customer type", () => "Retail"],
  ["Credit utilization", "Churn risk", value => value.match(/Churn probability:\s*([\d.]+%)/)?.[1] ?? "—"],
];

// --- Sidebar protection guard ---
// Ensures .dm-sidebar is never hidden. Scroll is always contained in .dm-content.
(function installSidebarGuard() {
  // Force demo mode OFF so the app always uses the real API, never mock data.
  try { localStorage.setItem("dm-demo-mode", "0"); } catch (e) {}

  function injectStyle() {
    if (document.getElementById("dm-sidebar-guard-style")) return;
    const s = document.createElement("style");
    s.id = "dm-sidebar-guard-style";
    // Keep the sidebar flex-shrink:0 and visible.
    // Do NOT use position:sticky inside overflow:hidden — it makes the element disappear.
    // Ensure dm-content absorbs all scroll so dm-layout never scrolls.
    s.textContent =
      ".dm-sidebar { flex-shrink: 0 !important; }" +
      ".dm-content { overflow-y: auto !important; min-height: 0 !important; }" +
      ".retail-churn-page { min-width: 0; width: 100%; }";
    (document.head || document.documentElement).appendChild(s);
  }

  function forceSidebarVisible() {
    document.querySelectorAll(".dm-sidebar").forEach(function(sidebar) {
      if (sidebar.style.display    === "none")   sidebar.style.display    = "";
      if (sidebar.style.visibility === "hidden") sidebar.style.visibility = "";
    });
  }

  injectStyle();
  forceSidebarVisible();

  // Watch only for sidebar style changes (not all elements — that caused churn).
  new MutationObserver(function(records) {
    var needsRestore = records.some(function(r) {
      return r.target.classList && r.target.classList.contains("dm-sidebar");
    });
    if (needsRestore) forceSidebarVisible();
  }).observe(document.documentElement, { attributes: true, subtree: true, attributeFilter: ["style"] });
})();

import Chart from "chart.js/auto";

// Remove the retired Security Posture area from the UI and redirect stale links.
(function removeSecurityPostureUI() {
  function hideSecurityControls() {
    if (/^#\/security(?:\/|$)/i.test(window.location.hash)) {
      window.location.hash = "#/exec/overview";
      return;
    }
    document.querySelectorAll("button, a, [role='button'], [role='tab']").forEach(control => {
      const label = (control.innerText || control.textContent || "").replace(/\s+/g, " ").trim();
      const isSecurityNav = /^security posture$/i.test(label);
      const isSecurityAgent = /^security(?: agent)?$/i.test(label) &&
        Boolean(control.closest(".agent-switcher, .ap-picker"));
      if (isSecurityNav || isSecurityAgent) {
        control.style.display = "none";
        control.setAttribute("aria-hidden", "true");
        control.setAttribute("tabindex", "-1");
      }
    });
    document.querySelectorAll(".card").forEach(card => {
      const title = card.querySelector(".section-title")?.textContent.replace(/\s+/g, " ").trim() || "";
      if (/^security posture$/i.test(title)) card.style.display = "none";
    });
    document.querySelectorAll(".ap-card").forEach(card => {
      if (/^security agent\b/i.test(card.querySelector("b")?.textContent.trim() || "")) {
        card.style.display = "none";
        card.setAttribute("aria-hidden", "true");
      }
    });
    const activeSecurityScope = document.querySelector(".dm-agent .agent-switcher-btn.active");
    if (/^security$/i.test(activeSecurityScope?.textContent.trim() || "")) {
      [...document.querySelectorAll(".dm-agent .agent-switcher-btn")]
        .find(button => /data intelligence/i.test(button.textContent))?.click();
    }
  }
  hideSecurityControls();
  new MutationObserver(hideSecurityControls).observe(document.documentElement, { childList: true, subtree: true });
})();

// Keep the Executive AI prior-month trust summary compact, matching the
// Jan–Nov range shown in the dashboard reference.
(function compactPriorMonthsSummary() {
  function patchSummary() {
    document.querySelectorAll("p, span, div").forEach(element => {
      if (element.children.length) return;
      const text = element.textContent.trim();
      if (/^Mean of January, February, March, April, May, June, July, August, September, October, November\.?$/.test(text)) {
        element.textContent = "Mean of Jan–Nov";
      }
    });
  }

  if (document.body) {
    new MutationObserver(patchSummary).observe(document.body, { childList: true, subtree: true });
    patchSummary();
  }
})();

const chartSeries = new WeakMap();
const metricData = new Map();
const metricTitles = ["Retail sales", "Units sold", "Inventory remaining", "Freshness delay (days)"];

// Override Chart.js line chart defaults: no point bubbles on simple trend charts.
// Runs once after Chart.js is available on the window object.
(function applyChartDefaults() {
  function tryApply() {
    const C = window.Chart;
    if (!C || !C.defaults) { setTimeout(tryApply, 300); return; }
    // Remove point dots from all Line datasets globally
    if (C.defaults.datasets && C.defaults.datasets.line) {
      C.defaults.datasets.line.pointRadius      = 0;
      C.defaults.datasets.line.pointHoverRadius = 3;
    } else {
      C.defaults.pointRadius      = 0;
      C.defaults.pointHoverRadius = 3;
    }
  }
  tryApply();
})();
const chartOverlays = new Map();
let extraMetricsLoaded = false;
let extraMetricRetryCount = 0;
let metricDataReady = false;
const retailChurnCacheKey = "deltamax.retail.churn.dashboard.v1";
function readRetailChurnCache() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(retailChurnCacheKey) || "null");
    return saved?.report && Date.now() - saved.savedAt < 15 * 60 * 1000 ? saved.report : null;
  } catch { return null; }
}
let retailChurnReportCache = readRetailChurnCache();
let retailChurnRequest = null;
let retailChurnLoadError = null;
const alertReturnKey = "deltamax-alert-return-position";

function requestRetailChurnReport(force = false) {
  if (!force && retailChurnReportCache) return Promise.resolve(retailChurnReportCache);
  if (retailChurnRequest) return retailChurnRequest;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  retailChurnRequest = fetch("/api/churn-risk?limit=10000&format=dashboard", {
    cache: "no-store", signal: controller.signal,
  }).then(response => {
    if (!response.ok) throw new Error(`Retail churn API returned HTTP ${response.status}.`);
    return response.json();
  }).then(report => {
    if (!report || typeof report !== "object") throw new Error("Retail churn API returned an invalid response.");
    if (report.ready === false) throw new Error(report.hint || "Retail churn results are not ready yet.");
    retailChurnReportCache = report;
    retailChurnLoadError = null;
    try { sessionStorage.setItem(retailChurnCacheKey, JSON.stringify({ savedAt: Date.now(), report })); } catch {}
    return report;
  }).catch(error => {
    retailChurnLoadError = error?.name === "AbortError"
      ? "Loading retail churn results timed out after 30 seconds. Check that the Retail API is running, then retry."
      : (error?.message || "Retail churn results could not be loaded. Check that the Retail API is running, then retry.");
    throw error;
  }).finally(() => {
    window.clearTimeout(timeout);
    retailChurnRequest = null;
  });
  return retailChurnRequest;
}

// Warm the retail dashboard data while the user is on another Executive AI page.
// Churn Risk then opens immediately from the in-memory response cache.
requestRetailChurnReport(Boolean(retailChurnReportCache)).then(report => {
  const page = document.querySelector(".retail-churn-page");
  const host = page?.parentElement;
  if (page && host && window.location.hash.includes("/exec/churn")) renderRetailChurnDashboard(host, page, report);
}).catch(() => {});

function restoreAlertReturnPosition() {
  // Clear any hidden state left by the older scroll-restoration implementation.
  document.documentElement.style.visibility = "";
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(alertReturnKey) || "null"); } catch { saved = null; }
  if (!saved || saved.href !== window.location.href) return;
  let attempts = 0;
  const restore = () => {
    const alertCard = [...document.querySelectorAll(".card")].find(card =>
      card.querySelector(".section-title")?.textContent.trim().startsWith("Predictive alerts"));
    const row = alertCard && [...alertCard.querySelectorAll("tbody tr")]
      .find(candidate => candidate.textContent.trim() === saved.alertRow);
    const target = row || alertCard;
    if (target) {
      target.scrollIntoView({ block: row ? "center" : "start", behavior: "instant" });
      if (row) row.focus({ preventScroll: true });
      sessionStorage.removeItem(alertReturnKey);
      return;
    }
    if (++attempts < 20) setTimeout(restore, 200);
    else {
      sessionStorage.removeItem(alertReturnKey);
    }
  };
  requestAnimationFrame(restore);
}

function getChartForCanvas(canvas) {
  const fiberKey = Object.keys(canvas).find(key => key.startsWith("__reactFiber$"));
  if (!fiberKey) return null;
  let fiber = canvas[fiberKey];
  while (fiber) {
    let hook = fiber.memoizedState;
    while (hook) {
      const value = hook.memoizedState;
      const candidate = value?.current;
      if (candidate?.canvas === canvas && candidate?.data?.labels && typeof candidate.update === "function") {
        return candidate;
      }
      hook = hook.next;
    }
    fiber = fiber.return;
  }
  return null;
}

function readChart(card) {
  const canvas = card?.querySelector("canvas");
  const chart = canvas && getChartForCanvas(canvas);
  if (!chart) return null;
  if (!chartSeries.has(chart)) chartSeries.set(chart, {
      labels: [...chart.data.labels],
      datasets: chart.data.datasets.map(dataset => ({ ...dataset, data: [...dataset.data] })),
    });
  return { chart, original: chartSeries.get(chart) };
}

function applyMetric(card, metric) {
  const source = metricData.get(metric);
  if (!source) return false;
  return drawMetricChart(card, metric, source);
}

let retailArimaForecastPromise;
function patchRetailArimaChart() {
  const isExecutiveTrustView = /\/exec\/(?:overview|forecast)/.test(window.location.hash);
  if (!isExecutiveTrustView) return;
  const heading = [...document.querySelectorAll(".section-title")].find(node =>
    /overall trust.*(?:monthly actual|forecast)/i.test(node.textContent)
  );
  const canvas = heading?.closest(".card")?.querySelector("canvas");
  if (!canvas) return;

  if (!retailArimaForecastPromise) {
    retailArimaForecastPromise = fetch("/api/forecast")
      .then(response => { if (!response.ok) throw new Error("Could not load retail ARIMA data"); return response.json(); })
      .then(report => (report.series || []).find(series => series.is_overall || series.key === "overall_trust_score"))
      .catch(() => null);
  }
  retailArimaForecastPromise.then(series => {
    if (!series || !canvas.isConnected || !/\/exec\/(?:overview|forecast)/.test(window.location.hash)) return;
    const actual = (series.hist_values || []).map(Number);
    const forecast = (series.forecast_values || []).map(Number);
    const actualLabels = series.hist_labels || [];
    const forecastLabels = series.forecast_labels || [];
    if (!actual.length || !forecast.length) return;
    const labels = [...actualLabels, ...forecastLabels];
    const lastActualIndex = actual.length - 1;
    const actualData = [...actual, ...forecast.map(() => null)];
    const forecastData = Array(labels.length).fill(null);
    forecastData[lastActualIndex] = actual.at(-1);
    forecast.forEach((value, index) => { forecastData[actual.length + index] = value; });

    const recent = actual.slice(-6);
    const mean = recent.reduce((sum, value) => sum + value, 0) / recent.length;
    const deviation = Math.sqrt(recent.reduce((sum, value) => sum + (value - mean) ** 2, 0) / Math.max(1, recent.length - 1));
    const lower = Array(labels.length).fill(null);
    const upper = Array(labels.length).fill(null);
    lower[lastActualIndex] = actual.at(-1);
    upper[lastActualIndex] = actual.at(-1);
    forecast.forEach((value, index) => {
      const providedLow = Number(series.lower_95?.[index]);
      const providedHigh = Number(series.upper_95?.[index]);
      const hasBand = Number.isFinite(providedLow) && Number.isFinite(providedHigh) && providedHigh > providedLow;
      const margin = Math.max(0.7, deviation * 0.9 * Math.sqrt(index + 1));
      lower[actual.length + index] = hasBand ? providedLow : value - margin;
      upper[actual.length + index] = hasBand ? providedHigh : value + margin;
    });

    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const displayMonth = (value, forecastMonth) => {
      const [year, month] = String(value).split("-");
      const name = monthNames[Number(month) - 1] || value;
      return forecastMonth ? `${name} ${String(year).slice(-2)}` : name;
    };
    const titleText = [...heading.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
    if (titleText) titleText.textContent = "Overall trust — monthly actual & ARIMA forecast";

    const previous = Chart.getChart(canvas);
    if (previous?.$retailArimaChart) return;
    previous?.destroy();
    const chart = new Chart(canvas, {
      type: "line",
      data: { labels: labels.map((label, index) => displayMonth(label, index >= actual.length)), datasets: [
        { label: "95% lower bound", data: lower, borderColor: "transparent", pointRadius: 0, fill: false, order: 4 },
        { label: "95% confidence interval", data: upper, borderColor: "transparent", backgroundColor: "rgba(255,152,0,0.11)", pointRadius: 0, fill: { target: 0, above: "rgba(255,152,0,0.11)" }, order: 3 },
        { label: "Actual", data: actualData, borderColor: "#159b62", backgroundColor: "#159b62", borderWidth: 2.5, tension: 0.28, pointRadius: context => context.dataIndex === lastActualIndex ? 5 : 2.5, pointBackgroundColor: context => context.dataIndex === lastActualIndex ? "#2563eb" : "#159b62", pointBorderColor: context => context.dataIndex === lastActualIndex ? "#2563eb" : "#159b62", spanGaps: false, order: 1 },
        { label: "Forecast", data: forecastData, borderColor: "#ff9800", backgroundColor: "#fff", borderWidth: 2, borderDash: [6, 5], tension: 0.18, pointRadius: context => context.dataIndex === lastActualIndex ? 0 : 3.5, pointStyle: "circle", pointBackgroundColor: "#fff", pointBorderColor: "#ff9800", pointBorderWidth: 2, spanGaps: false, order: 2 },
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "nearest", intersect: false },
        plugins: {
          tooltip: { enabled: true, backgroundColor: "#111827", titleColor: "#fff", bodyColor: "#fff", callbacks: { label: context => `${context.dataset.label}: ${Number(context.parsed.y).toFixed(1)}` } },
          legend: { position: "bottom", labels: { usePointStyle: false, filter: item => item.text === "Actual" || item.text === "Forecast" } },
        },
        scales: {
          x: { grid: { color: "rgba(148,163,184,0.12)" }, ticks: { maxRotation: 0, autoSkip: false, color: "#888", font: { size: 11 } }, border: { display: false } },
          y: { min: 70, max: 100, ticks: { stepSize: 5, color: "#888", callback: value => value }, grid: { color: "rgba(148,163,184,0.16)" }, border: { display: false } },
        },
      },
    });
    chart.$retailArimaChart = true;
  });
}

function positionChartOverlays() {
  for (const [card, overlay] of chartOverlays) {
    const container = card.querySelector(".fc-chart");
    if (!card.isConnected || !container) {
      overlay.remove();
      chartOverlays.delete(card);
      continue;
    }
    const rect = container.getBoundingClientRect();
    Object.assign(overlay.style, {
      left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`,
    });
    const canvas = container.querySelector("canvas");
    if (canvas) canvas.style.visibility = "hidden";
  }
}

window.__retailPositionChartOverlays = positionChartOverlays;

function drawMetricChart(card, metric, source) {
  const container = card?.querySelector(".fc-chart");
  if (!container || !source.labels?.length || !source.values?.length) return false;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 740 270");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${metric} by month`);
  svg.style.cssText = "display:block;width:100%;height:100%;overflow:hidden";
  const width = 740, left = 58, right = 14, top = 12, bottom = 62;
  const plotWidth = width - left - right, plotHeight = 270 - top - bottom;
  const values = source.values.map(value => Number(value) || 0);
  const forecast = (source.forecastValues || []).map(value => Number(value) || 0);
  const low = (source.lower || []).map(value => Number(value) || 0);
  const high = (source.upper || []).map(value => Number(value) || 0);
  const threshold = source.threshold !== null && source.threshold !== undefined && Number.isFinite(Number(source.threshold)) ? Number(source.threshold) : null;
  const allValues = [...values, ...forecast, ...low, ...high];
  if (threshold !== null) allValues.push(threshold);
  const min = Math.min(0, ...allValues), max = Math.max(...allValues);
  const step = (max - min || 1) / 4;
  const y = value => top + plotHeight - ((value - min) / (max - min || 1)) * plotHeight;
  const totalCount = values.length + forecast.length;
  const x = index => left + (totalCount < 2 ? plotWidth / 2 : index * plotWidth / (totalCount - 1));
  const add = (tag, attrs, text) => {
    const element = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    svg.appendChild(element);
    return element;
  };
  const smoothPath = points => {
    if (!points.length) return "";
    let path = `M ${points[0][0]} ${points[0][1]}`;
    for (let index = 1; index < points.length; index++) {
      const [x1, y1] = points[index - 1], [x2, y2] = points[index];
      const middle = (x1 + x2) / 2;
      path += ` C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`;
    }
    return path;
  };
  if (forecast.length && low.length === forecast.length && high.length === forecast.length) {
    const band = [[x(values.length - 1), y(values.at(-1))], ...high.map((value, index) => [x(values.length + index), y(value)]), ...low.map((value, index) => [x(values.length + low.length - index - 1), y(low[low.length - index - 1])])];
    add("polygon", { points: band.map(point => point.join(",")).join(" "), fill: "rgba(239,68,68,0.10)" });
  }
  for (let index = 0; index <= 4; index++) {
    const value = min + step * index;
    const yy = y(value);
    add("line", { x1: left, x2: width - right, y1: yy, y2: yy, stroke: "rgba(128,128,128,0.16)", "stroke-width": 1 });
    add("text", { x: left - 8, y: yy + 4, fill: "#888", "font-size": 11, "text-anchor": "end" }, Number(value.toPrecision(3)).toLocaleString());
  }
  if (threshold !== null) {
    const yy = y(threshold);
    add("line", { x1: left, x2: width - right, y1: yy, y2: yy, stroke: "#f59e0b", "stroke-width": 1.5, "stroke-dasharray": "2 4" });
  }
  add("path", { d: smoothPath(values.map((value, index) => [x(index), y(value)])), fill: "none", stroke: "#009d91", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" });
  if (forecast.length) {
    const forecastPoints = [[x(values.length - 1), y(values.at(-1))], ...forecast.map((value, index) => [x(values.length + index), y(value)])];
    add("path", { d: smoothPath(forecastPoints), fill: "none", stroke: "#ef4444", "stroke-width": 2.5, "stroke-dasharray": "5 5", "stroke-linecap": "round" });
    forecast.forEach((value, index) => add("circle", { cx: x(values.length + index), cy: y(value), r: 3, fill: "#fff", stroke: "#ef4444", "stroke-width": 2 }));
  }
  values.forEach((value, index) => {
    add("circle", { cx: x(index), cy: y(value), r: 3, fill: "#fff", stroke: "#009d91", "stroke-width": 2 });
  });
  const labels = [...source.labels, ...(source.forecastLabels || [])];
  labels.forEach((label, index) => {
    const monthIndex = Number(String(label).slice(5, 7)) - 1;
    const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][monthIndex] || String(label);
    const year = String(label).slice(2, 4);
    const isForecast = index >= values.length;
    const text = isForecast ? `${month} '${year}` : month;
    add("text", { x: x(index), y: 226, fill: "#888", "font-size": 10, "text-anchor": "end", transform: `rotate(-45 ${x(index)} 226)` }, text);
  });
  svg.classList.add("retail-metric-svg");
  const previous = chartOverlays.get(card);
  previous?.remove();
  const overlay = document.createElement("div");
  overlay.className = "retail-metric-chart-overlay";
  overlay.style.cssText = "position:fixed;z-index:2;pointer-events:none;overflow:hidden;background:var(--card,#fff)";
  overlay.appendChild(svg);
  document.body.appendChild(overlay);
  chartOverlays.set(card, overlay);
  positionChartOverlays();
  if (!window.retailChartOverlayListeners) {
    window.retailChartOverlayListeners = true;
    const reposition = () => window.__retailPositionChartOverlays?.();
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
  }
  return true;
}

function registerMetricCharts() {
  for (const head of document.querySelectorAll(".fc-head")) {
    const title = head.dataset.retailMetric;
    const card = head.closest(".card");
    const current = readChart(card);
    if (title && current && !metricData.has(title)) metricData.set(title, current.original);
  }
}

async function loadAdditionalMetrics() {
  if (extraMetricsLoaded) return;
  extraMetricsLoaded = true;
  try {
    const [forecastResponse, optionsResponse] = await Promise.all([
      fetch("/api/forecast"), fetch("/api/forecast/metric-options"),
    ]);
    if (!forecastResponse.ok || !optionsResponse.ok) throw new Error("Metric request failed");
    const [forecast, options] = await Promise.all([forecastResponse.json(), optionsResponse.json()]);
    const available = [
      ...(forecast.series || []).filter(series => series.kind === "metric").map(series => ({
        label: series.label, hist_labels: series.hist_labels, hist_values: series.hist_values,
        forecast_labels: series.forecast_labels, forecast_values: series.forecast_values,
        lower_95: series.lower_95, upper_95: series.upper_95, threshold: series.threshold,
      })),
      ...(options.metrics || []),
    ];
    for (const metric of available) {
      if (!metric.label || !Array.isArray(metric.hist_labels) || !Array.isArray(metric.hist_values)) continue;
      if (!metricTitles.includes(metric.label)) metricTitles.push(metric.label);
      metricData.set(metric.label, {
        labels: [...metric.hist_labels],
        values: [...metric.hist_values],
        forecastLabels: [...(metric.forecast_labels || [])],
        forecastValues: [...(metric.forecast_values || [])],
        lower: [...(metric.lower_95 || [])],
        upper: [...(metric.upper_95 || [])],
        threshold: metric.threshold,
      });
    }
    for (const select of document.querySelectorAll(".retail-metric-select")) {
      const current = new Set([...select.options].map(option => option.value));
      for (const metric of metricTitles) {
        if (current.has(metric)) continue;
        const option = document.createElement("option");
        option.value = metric;
        option.textContent = metric;
        select.appendChild(option);
      }
      select.disabled = false;
    }
    metricDataReady = true;
    for (const head of document.querySelectorAll(".fc-head[data-retail-metric]")) {
      const select = head.querySelector(".retail-metric-select");
      if (select) applyMetric(head.closest(".card"), select.value);
    }
  } catch (error) {
    extraMetricsLoaded = false;
    console.warn("Could not load additional retail chart metrics", error);
  }
}

function refreshMetricChoices() {
  // Keep every metric available. Selecting one already shown elsewhere swaps
  // the two charts, so the dashboard always shows four distinct measures.
}

function removeMetricForecastBadges() {
  for (const head of document.querySelectorAll(".fc-head")) {
    for (const meta of head.querySelectorAll(".fc-meta")) {
      for (const item of [...meta.children]) {
        if (item.textContent.trim().toUpperCase().includes("LOCAL DETERMINISTIC FORECAST")) item.style.display = "none";
      }
    }
  }
}

function openPredictiveAlert(row, table) {
  document.querySelectorAll(".retail-alert-modal-backdrop").forEach(existing => existing.remove());
  const headers = [...table.querySelectorAll("thead th")].map(cell => cell.textContent.trim());
  const values = [...row.querySelectorAll("td")].map(cell => cell.textContent.trim());
  const details = Object.fromEntries(headers.map((header, index) => [header.toLowerCase(), values[index] || ""]));
  const series = (details.series || "Predictive alert").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
  const trigger = (details.trigger || "Forecast threshold").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
  const severity = (details.severity || "MEDIUM").toUpperCase();
  const value = details.value || "—";
  const threshold = details.threshold || "—";
  const numericValue = Number(value.replace(/,/g, ""));
  const numericThreshold = Number(threshold.replace(/,/g, ""));
  const difference = Number.isFinite(numericValue) && Number.isFinite(numericThreshold) ? numericValue - numericThreshold : null;
  const severityColor = severity === "CRITICAL" || severity === "HIGH" ? "#c90016" : severity === "MEDIUM" ? "#a45a00" : "#176b87";
  const backdrop = document.createElement("div");
  backdrop.className = "retail-alert-modal-backdrop";
  backdrop.style.cssText = "position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(15,23,42,.48)";
  const dialog = document.createElement("section");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "retail-alert-modal-title");
  dialog.style.cssText = "width:min(1120px,100%);max-height:88vh;overflow:auto;padding:24px 28px;border:1px solid #e5e7eb;border-radius:16px;background:#fff;color:#111827;box-shadow:0 24px 70px rgba(15,23,42,.28)";
  const header = document.createElement("header");
  header.style.cssText = "display:flex;justify-content:space-between;align-items:flex-start;padding-bottom:20px;border-bottom:1px solid #e5e7eb";
  const titleWrap = document.createElement("div");
  const title = document.createElement("h2");
  title.id = "retail-alert-modal-title";
  title.textContent = series;
  title.style.cssText = "margin:0;font-size:22px;font-weight:650;display:flex;align-items:center;gap:10px";
  const bell = document.createElement("i");
  bell.className = "ti ti-bell-ringing";
  bell.setAttribute("aria-hidden", "true");
  bell.style.cssText = `color:${severityColor};font-size:21px`;
  title.prepend(bell);
  const subtitle = document.createElement("div");
  subtitle.textContent = `Predictive Alert • ${details.month || "Upcoming period"}`;
  subtitle.style.cssText = "margin-top:8px;font-size:13px;color:#737373";
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "Close alert details");
  close.style.cssText = "width:40px;height:40px;border:1px solid #e5e7eb;border-radius:10px;background:#fff;color:#737373;font-size:26px;line-height:1;cursor:pointer";
  titleWrap.append(title, subtitle);
  header.append(titleWrap, close);
  const body = document.createElement("div");
  body.style.cssText = "padding-top:24px";
  const badges = document.createElement("div");
  badges.style.cssText = "display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:30px";
  const severityBadge = document.createElement("span");
  severityBadge.textContent = severity;
  severityBadge.style.cssText = `padding:10px 16px;border-radius:6px;background:${severityColor};color:#fff;font-size:13px;font-weight:700`;
  const triggerBadge = document.createElement("span");
  triggerBadge.style.cssText = "padding:9px 14px;border:1px solid #e5e7eb;border-radius:6px;color:#262626;font-size:13px";
  const triggerStrong = document.createElement("strong");
  triggerStrong.textContent = "Trigger: ";
  triggerBadge.append(triggerStrong, document.createTextNode(trigger));
  badges.append(severityBadge, triggerBadge);
  const metrics = document.createElement("div");
  metrics.style.cssText = "display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;padding-bottom:20px;border-bottom:1px solid #e5e7eb";
  const metric = (label, metricValue, color = "#111827") => {
    const cell = document.createElement("div");
    const caption = document.createElement("div");
    caption.textContent = label;
    caption.style.cssText = "margin-bottom:7px;color:#737373;font-size:11px;text-transform:uppercase";
    const number = document.createElement("div");
    number.textContent = metricValue;
    number.style.cssText = `color:${color};font-size:23px;font-weight:700`;
    cell.append(caption, number);
    return cell;
  };
  const delta = difference === null ? "—" : `${difference >= 0 ? "+" : ""}${difference.toFixed(3)}`;
  const deltaColor = difference === null ? "#111827" : difference >= 0 ? "#11804a" : "#c90016";
  metrics.append(metric("Forecast value", value), metric("Threshold", threshold), metric("Delta from threshold", delta, deltaColor));
  const section = (label, text, accent = false) => {
    const wrap = document.createElement("section");
    wrap.style.cssText = "margin-top:24px";
    const heading = document.createElement("h3");
    heading.textContent = label;
    heading.style.cssText = "margin:0 0 10px;font-size:15px;font-weight:650";
    const content = document.createElement("div");
    content.textContent = text;
    content.style.cssText = accent
      ? "padding:16px 18px;border:1px solid #fecaca;border-left:3px solid #dc2626;border-radius:6px;font-size:14px;line-height:1.55"
      : "padding:16px 18px;border:1px solid #e5e7eb;border-radius:6px;color:#737373;font-size:14px;line-height:1.6";
    wrap.append(heading, content);
    return wrap;
  };
  const reason = details.reason || `The forecast value for ${series} in ${details.month || "this period"} crossed the alert trigger.`;
  const interpretation = `The forecast for ${series} in ${details.month || "this period"} is ${value}, compared with the threshold of ${threshold}. The alert was raised because the value met the ${trigger.toLowerCase()} condition.`;
  const why = section("ⓘ  Why This Alert", reason, true);
  const meaning = section("☼  Interpretation", interpretation);
  const actions = document.createElement("section");
  actions.style.cssText = "margin-top:22px";
  const actionTitle = document.createElement("h3");
  actionTitle.textContent = "➜  Recommended Actions";
  actionTitle.style.cssText = "margin:0 0 10px;font-size:15px;font-weight:650";
  const actionList = document.createElement("ul");
  actionList.style.cssText = "margin:0;padding-left:22px;color:#737373;font-size:14px;line-height:1.9";
  for (const action of [
    "Review recent data quality changes or anomalies in the source data.",
    "Check for upstream changes that may explain the forecast movement.",
    "Validate that the alert threshold is appropriate for this metric.",
    "Rerun the forecast after significant changes to the data.",
  ]) {
    const item = document.createElement("li");
    item.textContent = action;
    actionList.appendChild(item);
  }
  actions.append(actionTitle, actionList);
  const onKeyDown = event => {
    if (event.key === "Escape") dismiss();
  };
  const dismiss = () => {
    backdrop.style.display = "none";
    backdrop.setAttribute("aria-hidden", "true");
    document.removeEventListener("keydown", onKeyDown);
  };
  close.addEventListener("click", () => {
    dismiss();
    try {
      sessionStorage.setItem(alertReturnKey, JSON.stringify({
        href: window.location.href, alertRow: row.textContent.trim(),
      }));
    } catch { /* session storage may be unavailable in a restricted browser */ }
    window.location.reload();
  });
  backdrop.addEventListener("click", event => { if (event.target === backdrop) dismiss(); });
  body.append(badges, metrics, why, meaning, actions);
  dialog.append(header, body);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  close.focus();
  document.addEventListener("keydown", onKeyDown);
}

function openRetailRecordDetails(recordId, rowId = null) {
  document.querySelectorAll(".retail-record-modal-backdrop").forEach(node => node.remove());
  const backdrop = retailElement("div", undefined,
    "position:fixed;inset:0;z-index:10020;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(15,23,42,.52)",
    "retail-record-modal-backdrop");
  const dialog = retailElement("section", undefined,
    "display:flex;flex-direction:column;width:min(1080px,100%);max-height:90vh;overflow:hidden;border:1px solid #e4e4e7;border-radius:16px;background:#fff;color:#111827;box-shadow:0 24px 70px rgba(15,23,42,.28)");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", `Retail record ${recordId} details`);
  const header = retailElement("header", undefined, "display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:20px 24px;border-bottom:1px solid #e5e7eb");
  const title = retailElement("div", `Retail record #${recordId}`, "font-size:21px;font-weight:700");
  const titleLine = retailElement("div", undefined, "display:flex;align-items:center;gap:10px;flex-wrap:wrap");
  const severityBadge = retailElement("span", "", "display:none;padding:4px 10px;border:1px solid #fed7aa;border-radius:8px;background:#fff7ed;color:#c2410c;font-size:11px;font-weight:700");
  titleLine.append(title, severityBadge);
  const subtitle = retailElement("div", "Loading retail record details…", "margin-top:6px;color:#71717a;font-size:13px");
  const titleWrap = retailElement("div");
  titleWrap.append(titleLine, subtitle);
  const close = retailElement("button", "×", "width:40px;height:40px;flex:none;border:1px solid #e5e7eb;border-radius:10px;background:#fff;color:#737373;font-size:25px;line-height:1;cursor:pointer");
  close.type = "button";
  close.setAttribute("aria-label", "Close record details");
  header.append(titleWrap, close);
  const tabs = retailElement("nav", undefined, "display:flex;gap:4px;padding:0 20px;border-bottom:1px solid #e5e7eb");
  const body = retailElement("div", undefined, "overflow:auto;padding:20px 24px 24px");
  let detail = null;
  let activeTab = "trust";
  const closeModal = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKeyDown);
  };
  const onKeyDown = event => { if (event.key === "Escape") closeModal(); };
  close.addEventListener("click", closeModal);
  backdrop.addEventListener("click", event => { if (event.target === backdrop) closeModal(); });

  const addTab = (key, label, count = null) => {
    const button = retailElement("button", count === null ? label : `${label} (${count})`,
      `padding:12px 16px;border:0;border-bottom:2px solid ${activeTab === key ? "#009d91" : "transparent"};background:#fff;color:${activeTab === key ? "#00877d" : "#71717a"};font:inherit;font-size:14px;cursor:pointer`);
    button.type = "button";
    button.addEventListener("click", () => { activeTab = key; render(); });
    tabs.appendChild(button);
  };
  const render = () => {
    if (!detail) return;
    tabs.replaceChildren();
    addTab("trust", "Trust Analysis");
    const schema = detail.schema_validation || {};
    const schemaFields = detail.schema_fields || {};
    const schemaIssueCount = Number(schemaFields.anomalous_field_count) || 0;
    addTab("schema", "Schema", schemaFields.field_count ? schemaIssueCount : "—");
    body.replaceChildren();
    const raw = detail.raw_record || {};
    const isMissingValue = value => value === null || value === undefined ||
      (typeof value === "number" && !Number.isFinite(value)) ||
      (typeof value === "string" && ["", "nan", "none", "null", "<na>", "undefined"].includes(value.trim().toLowerCase()));
    const displayValue = (value, missingLabel = "Missing") => {
      if (isMissingValue(value)) return missingLabel;
      if (typeof value === "object") return JSON.stringify(value);
      return String(value);
    };
    const toFiniteNumber = value => isMissingValue(value) ? NaN : Number(value);
    if (activeTab === "schema") {
      const info = detail.record_info || {};
      const dataMonth = !isMissingValue(raw.data_month) ? raw.data_month : info.data_month;
      const saleDate = !isMissingValue(info.sale_date) ? info.sale_date : raw.sale_date;
      const headerFields = [
        ["Record ID", detail.record_id == null ? null : `#${detail.record_id}`],
        ["Transaction ID", info.transaction_id ?? raw.transaction_id],
        ["Shop ID", info.store_id ?? raw.store_id],
        ["Customer ID", info.customer_id ?? raw.customer_id],
        ["Product ID", info.product_id ?? raw.product_id],
        [!isMissingValue(dataMonth) ? "Data Month" : "Sale Date", !isMissingValue(dataMonth) ? dataMonth : saleDate],
        ["Lineage", detail.lineage],
      ].filter(([, value]) => !isMissingValue(value));
      const headerGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:9px;margin-bottom:16px");
      for (const [label, value] of headerFields) {
        const item = retailElement("div", undefined, "padding:10px 12px;border:1px solid #e5e7eb;border-radius:9px;background:#f8fafc;min-width:0");
        item.append(retailElement("div", label, "color:#71717a;font-size:11px"), retailElement("strong", displayValue(value), "display:block;margin-top:4px;font-size:13px;color:#171717;overflow-wrap:anywhere"));
        headerGrid.appendChild(item);
      }
      body.appendChild(headerGrid);

      const legend = retailElement("div", undefined, "display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 14px;margin-bottom:18px;border:1px solid #e5e7eb;border-radius:10px;background:#fff");
      const legendItem = (color, label, countText = "") => {
        const item = retailElement("div", undefined, "display:flex;align-items:center;gap:7px;color:#52525b;font-size:13px");
        item.append(retailElement("span", "", `width:10px;height:10px;border-radius:50%;background:${color}`), retailElement("span", `${label}${countText ? `: ${countText}` : ""}`));
        return item;
      };
      legend.append(
        legendItem("#dc2626", "Anomalous", `${schemaFields.anomalous_field_count ?? 0} fields`),
        legendItem("#14a38b", "Normal", `${schemaFields.normal_field_count ?? 0} fields`),
      );
      if (Number(schemaFields.calculated_field_count) > 0) legend.appendChild(legendItem("#9ca3af", "Calculated", `${schemaFields.calculated_field_count} fields`));
      if (Number(schemaFields.unverified_field_count) > 0) legend.appendChild(legendItem("#9ca3af", "Unverified", `${schemaFields.unverified_field_count} fields`));
      body.appendChild(legend);
      body.appendChild(retailElement("p", schema.available
        ? `Retail fields are checked against the configured schema and saved business-rule results from ${schema.source || "the configured source"}. Detector flags do not mark individual fields anomalous.`
        : schema.error || "Schema validation is unavailable; saved business-rule findings are still shown when available.", "margin:-8px 0 16px;color:#71717a;font-size:12px;line-height:1.5"));

      for (const category of schemaFields.categories || []) {
        body.appendChild(retailElement("h3", category.name, "margin:18px 0 9px;color:#b91c1c;font-size:14px;font-weight:750;text-transform:uppercase;letter-spacing:.035em"));
        const fieldGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:9px");
        for (const field of category.fields || []) {
          const anomalous = field.status === "ANOMALOUS";
          const unverified = field.status === "UNVERIFIED";
          const calculated = field.status === "CALCULATED";
          const card = retailElement("div", undefined, `min-width:0;padding:12px 14px;border:1px solid ${anomalous ? "#fca5a5" : "#e5e7eb"};border-left:3px solid ${anomalous ? "#dc2626" : "#e5e7eb"};border-radius:9px;background:${anomalous ? "#fff1f2" : "#fff"}`);
          const titleLine = retailElement("div", undefined, "display:flex;align-items:center;justify-content:space-between;gap:8px");
          titleLine.appendChild(retailElement("strong", field.name, `font-size:12px;color:${anomalous ? "#b91c1c" : "#52525b"};overflow-wrap:anywhere`));
          if (anomalous || calculated || unverified) {
            const badgeText = anomalous ? "ANOMALOUS" : calculated ? "CALCULATED" : "UNVERIFIED";
            const badgeColor = anomalous ? "#dc2626" : "#6b7280";
            titleLine.appendChild(retailElement("span", badgeText, `flex:none;padding:2px 7px;border:1px solid ${badgeColor}55;border-radius:999px;color:${badgeColor};font-size:9px;font-weight:750`));
          }
          card.append(titleLine, retailElement("div", displayValue(field.value), `margin-top:7px;font-size:14px;font-weight:600;color:${anomalous ? "#b91c1c" : "#171717"};overflow-wrap:anywhere;word-break:break-word`));
          if (field.expected_type) card.appendChild(retailElement("div", `Expected type: ${field.expected_type}`, "margin-top:5px;color:#9ca3af;font-size:10px"));
          for (const reason of field.reasons || []) card.appendChild(retailElement("div", reason, "margin-top:6px;color:#b91c1c;font-size:11px;line-height:1.45;overflow-wrap:anywhere"));
          fieldGrid.appendChild(card);
        }
        body.appendChild(fieldGrid);
      }
      if (!(schemaFields.categories || []).length) body.appendChild(retailElement("p", "No Retail fields are available for this record.", "color:#71717a;font-size:13px"));
      if (schema.available && schemaIssueCount === 0) body.appendChild(retailElement("p", "No schema or business-rule field anomalies were found for this transaction.", "margin:14px 0 0;color:#00875a;font-size:13px"));
      return;
    }

    const riskValue = toFiniteNumber(detail.overall_anomaly_risk);
    const riskColor = Number.isFinite(riskValue) && riskValue >= 50 ? "#c90016" : Number.isFinite(riskValue) && riskValue >= 20 ? "#a45a00" : "#176b87";
    const summary = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:14px;margin-bottom:18px;border:1px solid #e5e7eb;border-radius:12px;background:#f8fafc");
    for (const [label, value] of [
      ["Record ID", `#${detail.record_id}`],
      ["Transaction ID", displayValue(raw.transaction_id, "—")],
      ["Shop ID", displayValue(raw.store_id, "—")],
      ["Customer ID", displayValue(raw.customer_id, "—")],
      ["Product ID", displayValue(raw.product_id, "—")],
      ["Product", displayValue(raw.product_name, "—")],
      ["Sale date", displayValue(raw.sale_date, "—")],
      ["Lineage", displayValue(detail.lineage, "Unknown")],
      ["Anomaly risk", Number.isFinite(riskValue) ? `${riskValue}%` : "—"],
    ]) {
      const cell = retailElement("div", undefined, "min-width:0");
      cell.append(retailElement("div", label, "margin-bottom:4px;color:#71717a;font-size:11px"), retailElement("strong", String(value), `font-size:14px;color:${label === "Anomaly risk" ? riskColor : "#111827"};overflow-wrap:anywhere`));
      summary.appendChild(cell);
    }
    body.appendChild(summary);
    const metrics = detail.trust_metrics || {};
    const metricGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:18px");
    for (const [key, label] of [["overall_trust", "Overall Retail Trust"], ["anomaly_health", "Anomaly Health"], ["drift_health", "Drift Health"], ["business_health", "Business Health"], ["freshness_health", "Freshness Health"]]) {
      const value = metrics[key];
      const numericMetric = toFiniteNumber(value);
      const hasMetric = Number.isFinite(numericMetric);
      const cell = retailElement("div", undefined, "padding:12px;border:1px solid #e5e7eb;border-radius:10px;text-align:center");
      cell.append(retailElement("div", label, "color:#71717a;font-size:12px"), retailElement("strong", hasMetric ? `${numericMetric}` : "—", `display:block;margin-top:6px;font-size:20px;color:${hasMetric && numericMetric < 75 ? "#c90016" : "#00875a"}`));
      metricGrid.appendChild(cell);
    }
    body.appendChild(retailElement("h3", `Trust and quality metrics (threshold ${detail.trust_threshold ?? 75})`, "margin:0 0 10px;font-size:15px"));
    body.appendChild(metricGrid);
    const trustValue = toFiniteNumber(metrics.overall_trust);
    const trustNote = Number.isFinite(trustValue)
      ? `Trust threshold is ${detail.trust_threshold ?? 75}. This record is ${trustValue < (detail.trust_threshold ?? 75) ? "below" : "above"} the threshold; it is shown here because its retail anomaly risk is elevated.`
      : "An overall trust score is not available for this transaction.";
    body.appendChild(retailElement("p", trustNote, "margin:0 0 16px;color:#71717a;font-size:13px;line-height:1.5"));
    const explainGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:20px");
    const explainCard = (titleText, message) => {
      const box = retailElement("div", undefined, "padding:14px 16px;border:1px solid #e5e7eb;border-radius:11px;background:#f8fafc");
      box.append(retailElement("strong", titleText, "font-size:14px"), retailElement("p", message, "margin:7px 0 0;color:#71717a;font-size:13px;line-height:1.55"));
      return box;
    };
    explainGrid.append(
      explainCard(`Trust (${Number.isFinite(trustValue) ? trustValue.toFixed(1) : "—"})`, "How reliable is this transaction overall? This 0–100 score blends anomaly, drift, business, and freshness health. Higher is better."),
      explainCard(`Anomaly risk (${Number.isFinite(riskValue) ? riskValue.toFixed(1) : "—"})`, "How strongly do the retail detectors identify unusual behavior? The weighted detector score runs from 0 to 100. Higher means more unusual.")
    );
    body.appendChild(explainGrid);
    body.appendChild(retailElement("h3", `What's anomalous (${detail.fired_count ?? detail.detector_fired_count ?? 0})`, "margin:0 0 10px;font-size:16px"));
    const checks = retailElement("div", undefined, "display:grid;gap:10px");
    for (const detector of detail.detectors || []) {
      const flagged = Boolean(detector.fired);
      const evaluated = detector.evaluated !== false;
      const status = !evaluated ? "N/A" : flagged ? "FLAGGED" : "PASSED";
      const statusColor = !evaluated ? "#71717a" : flagged ? "#dc101b" : "#00875a";
      const statusBorder = !evaluated ? "#d4d4d8" : flagged ? "#fb7185" : "#86efac";
      const statusBackground = !evaluated ? "#f4f4f5" : flagged ? "#fff1f2" : "#f0fdf4";
      const displayName = detector.label || "Retail anomaly check";
      const card = retailElement("div", undefined, `display:flex;align-items:flex-start;gap:14px;padding:15px 18px;border:1px solid ${statusBorder};border-radius:12px;background:${statusBackground}`);
      card.appendChild(retailElement("span", flagged ? "⚠" : evaluated ? "✓" : "–", `flex:0 0 22px;padding-top:1px;text-align:center;font-size:19px;font-weight:700;color:${flagged ? "#ef111b" : evaluated ? "#36b8ae" : "#71717a"}`));
      const description = retailElement("div", undefined, "flex:1;min-width:0");
      const heading = retailElement("div", undefined, "display:flex;align-items:center;gap:10px;flex-wrap:wrap");
      heading.append(retailElement("strong", displayName, "font-size:16px;font-weight:700;color:#171717"), retailElement("span", status, `padding:2px 9px;border:1px solid ${statusBorder};border-radius:999px;color:${statusColor};font-size:11px;font-weight:700`));
      const limitLine = retailElement("div", undefined, "margin-top:7px;color:#737373;font-size:13px");
      limitLine.append(retailElement("span", "Limit: "), retailElement("code", detector.threshold || "detector flag = 1", "color:#52525b;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px"));
      description.append(heading,
        retailElement("div", detector.meaning || "Retail anomaly check.", "margin-top:6px;color:#737373;font-size:14px;line-height:1.5"),
        limitLine);
      const diagnostics = {
        isolation_forest: detail.isolation_forest_details,
        iqr: detail.iqr_details,
        knn: detail.knn_details,
        duplicate: detail.duplicate_details,
        missing: detail.missing_values_details,
        arima_forecast: detail.arima_details,
      }[detector.key];
      if (diagnostics) {
        const disclosure = retailElement("details", undefined, "margin-top:10px;padding-top:8px;border-top:1px solid #e5e7eb");
        disclosure.appendChild(retailElement("summary", "View actual calculation details", "color:#334155;font-size:12px;font-weight:650;cursor:pointer"));
        const detailBody = retailElement("div", undefined, "display:grid;gap:7px;margin-top:9px;color:#52525b;font-size:12px;line-height:1.5");
        const addDiagnosticLine = (label, value) => {
          if (value === null || value === undefined || value === "") return;
          const line = retailElement("div");
          line.append(retailElement("strong", `${label}: `, "color:#30343b"), retailElement("span", String(value)));
          detailBody.appendChild(line);
        };
        const numberText = value => {
          const parsed = Number(value);
          return value === null || value === undefined || !Number.isFinite(parsed) ? "Unavailable" : parsed.toPrecision(7).replace(/(?:\.0+|(?:(\.\d*?)0+))$/, "$1");
        };
        if (detector.key === "isolation_forest") {
          addDiagnosticLine("Decision score", numberText(diagnostics.score));
          addDiagnosticLine("Comparison", `${numberText(diagnostics.score)} < ${numberText(diagnostics.decision_threshold)} → ${diagnostics.status}`);
          addDiagnosticLine("Estimators", diagnostics.n_estimators);
          addDiagnosticLine("Contamination", diagnostics.contamination);
          addDiagnosticLine("Fit population", diagnostics.fit_population_count);
          addDiagnosticLine("Explanation", diagnostics.explanation);
        } else if (detector.key === "iqr") {
          addDiagnosticLine("Baseline lineage", (diagnostics.baseline_lineage || []).join(", "));
          addDiagnosticLine("Configured multiplier", diagnostics.multiplier);
          if (!diagnostics.available) addDiagnosticLine("Diagnostics", "Saved fences are unavailable. Rerun 03_Trust_Score.py.");
          const fields = diagnostics.fields || [];
          if (fields.length) {
            const wrap = retailElement("div", undefined, "overflow:auto;border:1px solid #e5e7eb;border-radius:8px");
            const table = retailElement("table", undefined, "min-width:760px;width:100%;border-collapse:collapse;font-size:11px");
            const headers = ["Field", "Value", "Q1", "Q3", "IQR", "Multiplier", "Lower fence", "Upper fence", "Result"];
            const head = retailElement("tr", undefined, "background:#f8fafc;text-align:left");
            headers.forEach(label => head.appendChild(retailElement("th", label, "padding:7px;border-bottom:1px solid #e5e7eb;white-space:nowrap")));
            table.appendChild(head);
            fields.forEach(field => {
              const row = retailElement("tr");
              const values = [field.field, numberText(field.actual_value), numberText(field.q1), numberText(field.q3), numberText(field.iqr), numberText(field.multiplier), numberText(field.lower_fence), numberText(field.upper_fence), field.status];
              values.forEach((value, index) => row.appendChild(retailElement("td", value, `padding:7px;border-bottom:1px solid #f0f1f3;color:${index === values.length - 1 ? field.status === "FLAGGED" ? "#dc101b" : field.status === "PASSED" ? "#00875a" : "#71717a" : "#52525b"};white-space:nowrap`)));
              table.appendChild(row);
            });
            wrap.appendChild(table);
            detailBody.appendChild(wrap);
          }
          addDiagnosticLine("Overall result", diagnostics.status);
        } else if (detector.key === "knn") {
          addDiagnosticLine("Nearest neighbours", diagnostics.neighbours);
          addDiagnosticLine("Actual mean distance", numberText(diagnostics.actual_distance));
          addDiagnosticLine("Percentile cutoff", `${numberText(Number(diagnostics.percentile) * 100)}th percentile · ${numberText(diagnostics.cutoff)}`);
          addDiagnosticLine("Reference population", diagnostics.reference_population_count);
          addDiagnosticLine("Cutoff condition", `${diagnostics.comparison_value || `${numberText(diagnostics.actual_distance)} > ${numberText(diagnostics.cutoff)}`} (${diagnostics.comparison_true ? "true" : "false"}) · ${diagnostics.status}`);
          addDiagnosticLine("Preprocessing", diagnostics.explanation);
        } else if (detector.key === "duplicate") {
          addDiagnosticLine("Key fields", (diagnostics.key_fields || []).join(", "));
          addDiagnosticLine("Current key values", JSON.stringify(diagnostics.current_key_values || {}));
          addDiagnosticLine("Matching records", diagnostics.matching_record_count);
          (diagnostics.matching_records || []).forEach(match => addDiagnosticLine("Match", `Record ${match.record_id ?? "?"} · ${match.transaction_id ?? "transaction ID unavailable"}`));
          addDiagnosticLine("Result", diagnostics.status);
        } else if (detector.key === "missing") {
          addDiagnosticLine("Required field count", diagnostics.required_field_count);
          addDiagnosticLine("Required fields", (diagnostics.required_fields || []).join(", "));
          addDiagnosticLine("Null count", diagnostics.null_count);
          addDiagnosticLine("Missing fields", (diagnostics.missing_fields || []).join(", ") || "None");
          addDiagnosticLine("Result", diagnostics.available ? diagnostics.status : "Unavailable; rerun 03_Trust_Score.py to save these diagnostics.");
        } else {
          addDiagnosticLine("Status", diagnostics.status);
          addDiagnosticLine("Monthly series", diagnostics.associated_month || "Unavailable");
          addDiagnosticLine("Explanation", diagnostics.message);
          const monthlyContext = diagnostics.monthly_series_context || [];
          if (monthlyContext.length) {
            addDiagnosticLine("Monthly actual context", monthlyContext.map(item => `${item.series} (${item.level}${item.level === "ALL" ? "" : ` ${item.entity_id}`}): ${numberText(item.value)} in ${item.month}`).join("; "));
          } else {
            addDiagnosticLine("Monthly actual context", "No matching monthly series row is available for this transaction.");
          }
        }
        disclosure.appendChild(detailBody);
        description.appendChild(disclosure);
      }
      card.appendChild(description);
      checks.appendChild(card);
    }
    body.appendChild(checks);

    const drift = detail.drift_distribution || {};
    const driftDetails = detail.drift_details || {};
    const driftSeverity = String(drift.severity || "Unavailable");
    const driftSeverityColor = driftSeverity === "Critical" || driftSeverity === "High" ? "#c90016" : driftSeverity === "Moderate" ? "#a45a00" : driftSeverity === "Low" ? "#00875a" : "#71717a";
    body.appendChild(retailElement("span", `Drift severity: ${driftSeverity}`, `display:inline-flex;margin:0 0 9px;padding:4px 9px;border:1px solid ${driftSeverityColor}55;border-radius:999px;color:${driftSeverityColor};font-size:11px;font-weight:700`));
    body.appendChild(retailElement("h3", "Drift — where this record sits", "margin:22px 0 8px;font-size:16px"));
    body.appendChild(retailElement("p", `${drift.explanation || "This stored record-level health score is compared with the selected lineage distribution."} The comparison contains ${Number(drift.sample_size || 0).toLocaleString()} ${rawLineage(drift.lineage || detail.lineage)} records.`, "margin:0 0 12px;color:#71717a;font-size:13px;line-height:1.5"));
    const bucketGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;align-items:end;padding:12px 8px 6px;border:1px solid #e5e7eb;border-radius:11px;background:#fff");
    const buckets = drift.buckets || [];
    const largestBucket = Math.max(1, ...buckets.map(bucket => Number(bucket.count) || 0));
    for (const bucket of buckets) {
      const selected = bucket.label === drift.record_bucket;
      const column = retailElement("div", undefined, "display:grid;grid-template-rows:auto 86px auto;align-items:end;gap:5px;text-align:center;min-width:0");
      column.appendChild(retailElement("span", Number(bucket.count || 0).toLocaleString(), `font-size:11px;color:${selected ? "#dc101b" : "#71717a"};font-weight:${selected ? "700" : "400"}`));
      const track = retailElement("div", undefined, "display:flex;align-items:flex-end;justify-content:center;height:86px");
      const bar = retailElement("div", undefined, `width:min(46px,70%);height:${Math.max(3, Math.round((Number(bucket.count || 0) / largestBucket) * 78))}px;border-radius:5px 5px 0 0;background:${selected ? "#dc101b" : "#cbd5e1"}`);
      track.appendChild(bar);
      column.append(track, retailElement("span", `${bucket.label}${selected ? " · this record" : ""}`, `font-size:10px;line-height:1.3;color:${selected ? "#dc101b" : "#71717a"};font-weight:${selected ? "700" : "400"}`));
      bucketGrid.appendChild(column);
    }
    body.appendChild(bucketGrid);
    const driftScoreValue = toFiniteNumber(drift.record_score);
    body.appendChild(retailElement("p", `This record's drift health is ${Number.isFinite(driftScoreValue) ? `${driftScoreValue.toFixed(1)}%` : "—"}${drift.percentile == null ? "" : ` (around the ${drift.percentile}th percentile in this lineage)`}.`, "margin:8px 0 18px;color:#71717a;font-size:12px"));
    const exactDrift = toFiniteNumber(driftDetails.health_score ?? drift.record_score_unrounded);
    body.appendChild(retailElement("p", `${Number.isFinite(exactDrift) ? `Unrounded stored drift health: ${exactDrift.toPrecision(12)} (${(exactDrift * 100).toPrecision(12)}%). ` : ""}${driftDetails.percentile_method || "The percentile uses the saved drift-health scores in this lineage."} The histogram and percentile use the same ${Number(driftDetails.population_count ?? drift.sample_size ?? 0).toLocaleString()}-record population.`, "margin:-10px 0 18px;color:#71717a;font-size:12px;line-height:1.55"));
    const cohort = drift.cohort_comparison || {};
    const cohortStats = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:9px;margin-bottom:16px");
    for (const [label, value] of [["Lineage records", cohort.records], ["PSI health", cohort.psi_health], ["Welch health", cohort.welch_health], ["Mahalanobis health", cohort.mahalanobis_health]]) {
      if (value === undefined || value === null) continue;
      const shown = label === "Lineage records" ? Number(value).toLocaleString() : `${(Number(value) * 100).toFixed(1)}%`;
      const stat = retailElement("div", undefined, "padding:10px 12px;border:1px solid #e5e7eb;border-radius:9px;background:#fff");
      stat.append(retailElement("div", label, "color:#71717a;font-size:11px"), retailElement("strong", shown, "display:block;margin-top:4px;font-size:14px;color:#111827"));
      cohortStats.appendChild(stat);
    }
    if (cohortStats.childElementCount) body.appendChild(cohortStats);
    if (Array.isArray(cohort.drifted_features) && cohort.drifted_features.length) {
      body.appendChild(retailElement("p", `Features with detected cohort drift: ${cohort.drifted_features.join(", ")}.`, "margin:0 0 16px;color:#a45a00;font-size:13px"));
    } else if (cohort.records !== undefined) {
      body.appendChild(retailElement("p", "No feature-level cohort drift was detected for this lineage.", "margin:0 0 16px;color:#00875a;font-size:13px"));
    }

    const rules = detail.business_rules || {};
    body.appendChild(retailElement("h3", "Business rules", "margin:20px 0 6px;font-size:16px"));
    body.appendChild(retailElement("p", `Breaks ${Number(rules.estimated_violations || 0)} of ${Number(rules.total_rules || 0)} retail rules:`, "margin:0 0 10px;color:#71717a;font-size:13px"));
    const ruleList = retailElement("div", undefined, "display:grid;gap:9px");
    for (const rule of rules.checks || []) {
      const flagged = Boolean(rule.fired);
      const ruleStatusColor = flagged ? "#dc101b" : "#00875a";
      const ruleCard = retailElement("div", undefined, `display:flex;align-items:flex-start;gap:13px;padding:13px 16px;border:1px solid ${flagged ? "#fb7185" : "#86efac"};border-radius:11px;background:${flagged ? "#fff1f2" : "#f0fdf4"}`);
      ruleCard.appendChild(retailElement("span", flagged ? "⚠" : "✓", `flex:0 0 20px;color:${flagged ? "#ef111b" : "#36b8ae"};font-size:18px;font-weight:700`));
      const ruleContent = retailElement("div", undefined, "flex:1;min-width:0");
      const ruleTitle = retailElement("div", undefined, "display:flex;align-items:center;gap:9px;flex-wrap:wrap");
      ruleTitle.append(retailElement("strong", rule.label, "font-size:14px;color:#171717"), retailElement("span", rule.severity || "RULE", "padding:2px 8px;border:1px solid #e5e7eb;border-radius:999px;color:#71717a;font-size:10px;font-weight:700"), retailElement("span", rule.result || (flagged ? "FLAGGED" : "PASSED"), `padding:2px 8px;border:1px solid ${flagged ? "#fb7185" : "#86efac"};border-radius:999px;color:${ruleStatusColor};font-size:10px;font-weight:700`));
      const ruleLimit = retailElement("div", undefined, "margin-top:6px;color:#71717a;font-size:12px;line-height:1.5");
      ruleLimit.append(retailElement("span", `${rule.detail} · Expected: `), retailElement("code", rule.threshold, "font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#52525b"));
      if (rule.configured_condition) ruleLimit.appendChild(retailElement("div", `Configured rule: ${rule.configured_condition}`, "margin-top:3px;color:#9298a2;font-size:11px"));
      ruleContent.append(ruleTitle, ruleLimit);
      ruleCard.appendChild(ruleContent);
      ruleList.appendChild(ruleCard);
    }
    body.appendChild(ruleList);
    const freshness = detail.freshness_details || detail.freshness || {};
    body.appendChild(retailElement("h3", "Freshness analysis", "margin:20px 0 8px;font-size:16px"));
    const freshnessGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px");
    for (const [label, value] of [["Transaction date", freshness.transaction_date], ["Expected arrival", freshness.expected_arrival_date], ["Data arrival", freshness.actual_arrival_date], ["Data age", freshness.record_age_days == null ? null : `${freshness.record_age_days} days`], ["Arrival delay", freshness.days_late == null ? null : `${freshness.days_late} days late`], ["Freshness health", freshness.health == null ? null : `${freshness.health}%`], ["Configured τ", freshness.tau_days == null ? null : `${freshness.tau_days} days`]]) {
      const item = retailElement("div", undefined, "padding:10px 12px;border:1px solid #e5e7eb;border-radius:9px;background:#fff");
      item.append(retailElement("div", label, "color:#71717a;font-size:11px"), retailElement("strong", displayValue(value, "—"), "display:block;margin-top:4px;font-size:13px;color:#111827;overflow-wrap:anywhere"));
      freshnessGrid.appendChild(item);
    }
    body.appendChild(freshnessGrid);
    const freshnessDays = toFiniteNumber(freshness.days_late);
    const freshnessHealth = toFiniteNumber(freshness.health);
    const freshnessText = Number.isFinite(freshnessDays) && Number.isFinite(freshnessHealth)
      ? `The data arrived ${freshnessDays} day(s) after the expected date. Freshness health is ${freshnessHealth.toFixed(1)} / 100, with exponential decay parameter τ = ${freshness.tau_days ?? detail.freshness_tau ?? 5} days.`
      : "Arrival timing or freshness health is unavailable for this transaction.";
    body.appendChild(retailElement("p", freshnessText, "margin:10px 0 0;color:#71717a;font-size:13px;line-height:1.5"));
    body.appendChild(retailElement("p", "Data Age measures time since the transaction date. Arrival Delay measures time between expected arrival and actual ingestion; only Arrival Delay affects Freshness Health.", "margin:6px 0 0;color:#71717a;font-size:12px;line-height:1.5"));
  };
  dialog.append(header, tabs, body);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  document.addEventListener("keydown", onKeyDown);
  const detailUrl = `/api/anomaly-record/${encodeURIComponent(recordId)}${rowId == null ? "" : `?row_id=${encodeURIComponent(rowId)}`}`;
  fetch(detailUrl, { cache: "no-store" })
    .then(response => { if (!response.ok) throw new Error("Could not load retail record details."); return response.json(); })
    .then(result => {
      if (!result.ready) throw new Error(result.error || "Retail record was not found.");
      detail = result;
      title.textContent = `Retail record #${detail.record_id || recordId}`;
      const severity = String(detail.severity || "MEDIUM").toUpperCase();
      const severityColor = severity === "HIGH" || severity === "CRITICAL" ? "#c90016" : severity === "MEDIUM" ? "#a45a00" : "#176b87";
      severityBadge.textContent = severity;
      severityBadge.style.cssText = `display:inline-flex;padding:4px 10px;border:1px solid ${severityColor}44;border-radius:8px;background:${severityColor}10;color:${severityColor};font-size:11px;font-weight:700`;
      subtitle.textContent = `${rawLineage(detail.lineage)}${detail.severity ? ` · ${detail.severity} risk` : ""}`;
      render();
    })
    .catch(error => { body.replaceChildren(retailElement("div", error.message || "Could not load record details.", "padding:18px;border:1px solid #fecaca;border-radius:10px;color:#b91c1c;background:#fff5f5")); });
  close.focus();
}

function rawLineage(value) {
  return String(value || "Unknown").toLowerCase().replace(/\b\w/g, letter => letter.toUpperCase()).replaceAll("_", " ");
}

function affectedRowSummary(row, table) {
  const headers = [...table.querySelectorAll("thead th")].map(cell => cell.textContent.trim().toLowerCase());
  const cells = [...row.querySelectorAll("td")].map(cell => cell.textContent.trim());
  const cellFor = (name, fallback) => {
    const index = headers.findIndex(header => header === name || header.includes(name));
    return cells[index >= 0 ? index : fallback] || "";
  };
  const number = value => {
    const parsed = Number(String(value).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/)?.[0]);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const recordId = cellFor("record", 1).replace(/^#/, "").trim();
  const firedText = cellFor("fired", 6);
  return {
    recordId,
    business: cellFor("business", 2).trim().toLowerCase(),
    lineage: cellFor("lineage", 3).replace(/[^a-z0-9]/gi, "").toLowerCase(),
    trust: number(cellFor("trust", 4)),
    risk: number(cellFor("risk", 5)),
    fired: number(firedText),
    severity: (firedText.match(/critical|high|medium|low/i)?.[0] || "").toUpperCase(),
  };
}

async function resolveAffectedRecord(row, table) {
  const visible = affectedRowSummary(row, table);
  const response = await fetch("/api/anomaly-records?limit=500", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not resolve the selected affected record.");
  const listing = await response.json();
  if (!listing.ready) throw new Error(listing.hint || "Affected record results are unavailable.");
  const candidates = (listing.records || []).filter(record => {
    if (String(record.record_id) !== visible.recordId) return false;
    if (visible.business && String(record.businessID || "").trim().toLowerCase() !== visible.business) return false;
    if (visible.lineage && String(record.lineage || "").replace(/[^a-z0-9]/gi, "").toLowerCase() !== visible.lineage) return false;
    if (visible.trust !== null && Math.abs(Number(record.trust) - visible.trust) > 0.11) return false;
    if (visible.risk !== null && Math.abs(Number(record.anomaly_risk) - visible.risk) > 0.11) return false;
    if (visible.fired !== null && Number(record.fired_count) !== visible.fired) return false;
    if (visible.severity && String(record.severity).toUpperCase() !== visible.severity) return false;
    return true;
  });
  if (!candidates.length) throw new Error(`Could not match record #${visible.recordId} to its exact scored row.`);
  const fingerprint = JSON.stringify(visible);
  const priorSameRows = [...table.querySelectorAll("tbody tr")]
    .slice(0, [...row.parentElement.children].indexOf(row))
    .filter(candidate => JSON.stringify(affectedRowSummary(candidate, table)) === fingerprint).length;
  const match = candidates[Math.min(priorSameRows, candidates.length - 1)];
  if (!match.row_id) throw new Error(`The scored row ID for record #${visible.recordId} is missing.`);
  return match;
}

function patchAffectedRecordRows() {
  if (!window.location.hash.includes("/exec/affected")) return;
  for (const card of document.querySelectorAll(".card")) {
    const heading = card.querySelector(".section-title");
    if (!heading?.textContent.toLowerCase().includes("most anomalous records")) continue;
    const table = card.querySelector("table");
    if (!table || table.dataset.retailRecordPopupBound) continue;
    table.dataset.retailRecordPopupBound = "true";
    table.style.cursor = "pointer";
    table.addEventListener("click", event => {
      const row = event.target.closest("tbody tr");
      if (!row || !table.contains(row)) return;
      const summary = affectedRowSummary(row, table);
      if (!summary.recordId) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      resolveAffectedRecord(row, table)
        .then(record => openRetailRecordDetails(record.record_id, record.row_id))
        .catch(error => openRetailRecordDetails(summary.recordId, "__unresolved__"));
    }, true);
    table.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tbody tr");
      if (!row || !table.contains(row)) return;
      const summary = affectedRowSummary(row, table);
      if (!summary.recordId) return;
      event.preventDefault();
      resolveAffectedRecord(row, table)
        .then(record => openRetailRecordDetails(record.record_id, record.row_id))
        .catch(error => openRetailRecordDetails(summary.recordId, "__unresolved__"));
    }, true);
    for (const row of table.querySelectorAll("tbody tr")) {
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.style.cursor = "pointer";
    }
  }
}

function patchPredictiveAlertRows() {
  for (const card of document.querySelectorAll(".card")) {
    const heading = card.querySelector(".section-title");
    if (!heading?.textContent.trim().startsWith("Predictive alerts")) continue;
    const table = card.querySelector("table");
    if (!table) continue;
    if (!table.dataset.alertPopupBound) {
      table.dataset.alertPopupBound = "true";
      table.style.cursor = "pointer";
      table.addEventListener("click", event => {
        const row = event.target.closest("tbody tr");
        if (row && table.contains(row)) openPredictiveAlert(row, table);
      });
      table.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const row = event.target.closest("tbody tr");
        if (!row || !table.contains(row)) return;
        event.preventDefault();
        openPredictiveAlert(row, table);
      });
    }
    for (const row of table.querySelectorAll("tbody tr")) {
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", `Open alert: ${row.textContent.trim()}`);
      row.style.cursor = "pointer";
    }
  }
}

function hideMonthOverMonthSection() {
  for (const card of document.querySelectorAll(".card")) {
    const heading = card.querySelector(".section-title");
    if (heading?.textContent.trim().startsWith("Month-over-month pillar change (%)")) {
      card.style.display = "none";
    }
  }
}

function restoreRetailChurnPage() {
  document.querySelectorAll(".retail-churn-page").forEach(page => {
    const host = page.parentElement;
    page.remove();
    if (!host) return;
    host.querySelectorAll(":scope > [data-retail-churn-original-display]").forEach(node => {
      node.style.display = node.dataset.retailChurnOriginalDisplay;
      delete node.dataset.retailChurnOriginalDisplay;
    });
    // Safety: ensure the sidebar is never left hidden after churn page removal.
    host.querySelectorAll(":scope > .dm-sidebar").forEach(node => {
      if (node.style.display === "none") node.style.display = "";
    });
  });
}

function hideNativeChurnContent(host, page) {
  for (const node of host.children) {
    if (node === page) continue;
    // Never hide structural layout elements: sidebar, nav, header, or the layout
    // container itself — hiding them causes the sidebar to disappear while scrolling.
    if (
      node.matches(
        ".dm-sidebar, .dm-layout, .dm-header, .dm-nav, aside, nav, header"
      )
    ) continue;
    if (node.dataset.retailChurnOriginalDisplay === undefined) {
      node.dataset.retailChurnOriginalDisplay = node.style.display;
    }
    node.style.display = "none";
  }
}

function restoreRetailChurnHost(host) {
  if (!host) return;
  host.querySelectorAll(":scope > [data-retail-churn-original-display]").forEach(node => {
    node.style.display = node.dataset.retailChurnOriginalDisplay;
    delete node.dataset.retailChurnOriginalDisplay;
  });
  // Safety: ensure the sidebar is never left hidden.
  host.querySelectorAll(":scope > .dm-sidebar").forEach(node => {
    if (node.style.display === "none") node.style.display = "";
  });
}

function openChurnCustomerModal(customer) {
  // Remove any existing modal
  document.querySelectorAll(".rc-customer-modal-backdrop").forEach(n => n.remove());

  const tier   = String(customer.risk_tier || "LOW").toUpperCase();
  const colors = { CRITICAL: "#e7000b", HIGH: "#fe9a00", WATCH: "#2563eb", LOW: "#169765" };
  const tierBg = { CRITICAL: "#fef2f2", HIGH: "#fff7ed", WATCH: "#eff6ff", LOW: "#f0fdf4" };
  const color  = colors[tier] || "#555";
  const bg     = tierBg[tier] || "#f9fafb";

  const fmtN   = v => Number(v || 0).toLocaleString();
  const fmtPct = v => `${Number(v || 0).toFixed(1)}%`;
  const fmtCcy = v => `£${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  // ── backdrop ───────────────────────────────────────────────────────────
  const backdrop = retailElement("div", undefined,
    "position:fixed;inset:0;z-index:10050;display:flex;align-items:center;" +
    "justify-content:center;padding:20px;background:rgba(15,23,42,0.45);backdrop-filter:blur(2px)",
    "rc-customer-modal-backdrop");

  // ── dialog ─────────────────────────────────────────────────────────────
  const dialog = retailElement("div", undefined,
    "width:min(680px,100%);max-height:90vh;overflow-y:auto;" +
    "background:#fff;border-radius:16px;border:1px solid #e5e7eb;" +
    "box-shadow:0 24px 60px rgba(15,23,42,0.22);display:flex;flex-direction:column");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");

  // ── header ─────────────────────────────────────────────────────────────
  const header = retailElement("div", undefined,
    "display:flex;align-items:flex-start;justify-content:space-between;" +
    "gap:12px;padding:22px 24px 18px;border-bottom:1px solid #f3f4f6");

  const titleWrap = retailElement("div");

  const nameRow = retailElement("div", undefined,
    "display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px");
  const nameEl = retailElement("div",
    customer.customer_name || `Customer ${customer.customer_id}`,
    "font-size:20px;font-weight:700;color:#111827");
  nameRow.appendChild(nameEl);

  const idSub = retailElement("div",
    [
      customer.customer_id,
      customer.customer_segment,
    ].filter(Boolean).join(" · "),
    "font-size:12px;color:#9ca3af;margin-top:2px");

  titleWrap.append(nameRow, idSub);

  const closeBtn = retailElement("button", "×",
    "width:36px;height:36px;flex-shrink:0;border:1px solid #e5e7eb;border-radius:8px;" +
    "background:#fff;color:#6b7280;font-size:22px;line-height:1;cursor:pointer");
  closeBtn.type = "button";
  closeBtn.setAttribute("aria-label", "Close");
  header.append(titleWrap, closeBtn);

  // ── body ───────────────────────────────────────────────────────────────
  const body = retailElement("div", undefined, "padding:20px 24px 24px;display:flex;flex-direction:column;gap:20px");

  // Risk badge row
  const badgeRow = retailElement("div", undefined, "display:flex;align-items:center;gap:14px;flex-wrap:wrap");
  const tierPill = retailElement("span", tier,
    `padding:7px 16px;border-radius:7px;background:${color};color:#fff;font-size:12px;font-weight:700;letter-spacing:.04em`);
  const probBadge = retailElement("span", undefined,
    "padding:7px 14px;border:1px solid #e5e7eb;border-radius:7px;font-size:13px;color:#374151");
  probBadge.append(
    retailElement("span", "Churn probability: ", "font-weight:500"),
    retailElement("span", fmtPct(customer.churn_probability), `font-weight:700;color:${color}`)
  );
  const lastBadge = retailElement("span", undefined,
    "padding:7px 14px;border:1px solid #e5e7eb;border-radius:7px;font-size:13px;color:#374151");
  lastBadge.append(
    retailElement("span", "Last purchase: ", "font-weight:500"),
    retailElement("span", customer.last_purchase_date || "—", "font-weight:600;color:#111827")
  );
  badgeRow.append(tierPill, probBadge, lastBadge);

  // Key metrics grid
  const metricsGrid = retailElement("div", undefined,
    "display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px");

  const mkMetric = (label, value) => {
    const cell = retailElement("div", undefined,
      "padding:14px 16px;border:1px solid #f1f5f9;border-radius:10px;background:#f8fafc");
    cell.append(
      retailElement("div", label, "font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;font-weight:500"),
      retailElement("div", value, "font-size:18px;font-weight:700;color:#111827")
    );
    return cell;
  };

  metricsGrid.append(
    mkMetric("Total purchases",     fmtN(customer.total_purchases)),
    mkMetric("Total spend",         fmtCcy(customer.total_purchase_amount)),
    mkMetric("Avg order value",     fmtCcy(customer.average_order_value)),
    mkMetric("Days since purchase", customer.recency_days != null ? `${customer.recency_days}d` : "—"),
    mkMetric("Return rate",         customer.return_rate != null ? fmtPct(customer.return_rate) : "—"),
    mkMetric("Avg days between",    customer.avg_days_between_purchases > 0 ? `${Math.round(customer.avg_days_between_purchases)}d` : "—")
  );

  // Product categories
  const cats = Array.isArray(customer.categories) && customer.categories.length
    ? customer.categories : [];
  if (cats.length) {
    const catSection = retailElement("div");
    catSection.appendChild(retailElement("div", "Product categories",
      "font-size:12px;font-weight:600;color:#374151;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em"));
    const catWrap = retailElement("div", undefined, "display:flex;flex-wrap:wrap;gap:7px");
    cats.forEach(c => {
      catWrap.appendChild(retailElement("span", c,
        "padding:4px 10px;border:1px solid #e5e7eb;border-radius:999px;font-size:12px;color:#374151;background:#f9fafb"));
    });
    catSection.appendChild(catWrap);
    body.appendChild(catSection);
  }

  // Why flagged box
  const flagBox = retailElement("div", undefined,
    "padding:14px 16px;border:1px solid #fecaca;border-left:4px solid #ef4444;" +
    "border-radius:8px;background:#fff7f7");
  flagBox.append(
    retailElement("div", "ⓘ  Why this customer is flagged",
      "font-size:13px;font-weight:650;color:#991b1b;margin-bottom:8px"),
    retailElement("div", customer.likely_reason || "—",
      "font-size:13px;color:#374151;line-height:1.6")
  );

  // Top risk factors list
  const factors = Array.isArray(customer.top_risk_factors) && customer.top_risk_factors.length
    ? customer.top_risk_factors
    : (customer.likely_reason || "").split("; ").filter(Boolean);

  const factorSection = retailElement("div");
  factorSection.appendChild(retailElement("div", "☰  Top risk factors",
    "font-size:13px;font-weight:650;color:#374151;margin-bottom:10px"));
  const ul = retailElement("ul", undefined,
    "margin:0;padding-left:20px;display:flex;flex-direction:column;gap:6px");
  factors.forEach(f => {
    ul.appendChild(retailElement("li", f, "font-size:13px;color:#4b5563;line-height:1.5"));
  });
  factorSection.appendChild(ul);

  body.append(badgeRow, metricsGrid, flagBox, factorSection);

  // ── wire close ─────────────────────────────────────────────────────────
  const dismiss = () => backdrop.remove();
  closeBtn.addEventListener("click", dismiss);
  backdrop.addEventListener("click", e => { if (e.target === backdrop) dismiss(); });
  document.addEventListener("keydown", function onKey(e) {
    if (e.key === "Escape") { dismiss(); document.removeEventListener("keydown", onKey); }
  });

  dialog.append(header, body);
  backdrop.appendChild(dialog);
  document.body.appendChild(backdrop);
  closeBtn.focus();
}

function renderRetailChurnDashboard(host, page, report) {
  if (!page.isConnected) return;
  page.replaceChildren();
  if (!report?.ready) {
    page.appendChild(retailElement("div", report?.hint || "Retail churn results are not available yet.",
      "padding:18px;border:1px solid #fecaca;border-radius:12px;background:#fff;color:#b91c1c"));
    return;
  }

  // ── constants ────────────────────────────────────────────────────────────
  const customers = report.customers || report.businesses || [];
  const counts    = report.tier_counts || {};
  const colors    = { CRITICAL: "#e7000b", HIGH: "#fe9a00", WATCH: "#2563eb", LOW: "#179765" };
  const tierBg    = { CRITICAL: "#fef2f2", HIGH: "#fff7ed", WATCH: "#eff6ff", LOW: "#f0fdf4" };
  const fmtN  = v => Number(v || 0).toLocaleString();
  const fmtPct = v => `${Number(v || 0).toFixed(1)}%`;

  // ── inject per-page styles once ──────────────────────────────────────────
  if (!document.getElementById("retail-churn-v2-style")) {
    const s = document.createElement("style");
    s.id = "retail-churn-v2-style";
    s.textContent = `
      .rc-page { min-width:0; }
      .rc-kpis  { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:18px; }
      .rc-kpi   { background:var(--card,#fff); border:1px solid var(--border,#e5e7eb);
                  border-radius:12px; padding:18px 20px; min-width:0; }
      .rc-kpi-icon { display:flex; align-items:center; gap:7px; font-size:12px;
                     color:var(--muted-foreground,#667085); margin-bottom:10px; font-weight:500; }
      .rc-kpi-icon i { font-size:15px; }
      .rc-kpi-val  { font-size:26px; font-weight:700; line-height:1.1; margin-bottom:4px; }
      .rc-kpi-sub  { font-size:12px; color:var(--muted-foreground,#667085); }
      .rc-two  { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }
      .rc-card { background:var(--card,#fff); border:1px solid var(--border,#e5e7eb);
                 border-radius:14px; padding:20px 22px; }
      .rc-card-title { display:flex; align-items:center; gap:8px; font-size:14px; font-weight:650;
                       color:var(--foreground,#111827); margin-bottom:16px; }
      .rc-card-title i { font-size:16px; }
      .rc-donut-wrap { display:flex; align-items:center; justify-content:flex-start;
                       gap:32px; flex-wrap:wrap; }
      .rc-legend { display:grid; gap:14px; }
      .rc-legend-row { display:grid; grid-template-columns:12px 1fr auto 38px;
                       gap:10px; align-items:center; font-size:13.5px; }
      .rc-legend-dot { width:12px; height:12px; border-radius:3px; flex-shrink:0; }
      .rc-legend-pct { color:#667085; text-align:right; font-size:12px; }
      .rc-driver-row { display:grid; grid-template-columns:minmax(140px,210px) 1fr 46px;
                       gap:12px; align-items:center; font-size:13px; color:#667085; margin-bottom:12px; }
      .rc-driver-track { height:22px; background:#f1f3f5; border-radius:4px; overflow:hidden; }
      .rc-driver-fill  { height:100%; background:#2563eb; border-radius:4px; }
      .rc-driver-pct   { text-align:right; font-weight:600; color:#1e293b; }
      .rc-bar-row  { display:grid; grid-template-columns:minmax(110px,160px) 1fr 40px;
                     gap:10px; align-items:center; font-size:13px; margin-bottom:11px; }
      .rc-bar-track { height:10px; background:#f1f3f5; border-radius:999px; overflow:hidden; }
      .rc-bar-fill  { height:100%; border-radius:999px; }
      .rc-seg-row   { display:grid; grid-template-columns:minmax(110px,160px) 1fr 40px;
                      gap:10px; align-items:center; font-size:13px; margin-bottom:11px; }
      .rc-seg-track { height:10px; background:#f1f3f5; border-radius:999px; overflow:hidden; }
      .rc-tier-pill { display:inline-block; padding:3px 10px; border-radius:6px;
                      font-size:11px; font-weight:700; letter-spacing:.03em; }
      .rc-table { width:100%; border-collapse:collapse; font-size:13px; }
      .rc-table th { padding:9px 12px; text-align:left; font-size:11px; font-weight:600;
                     color:#667085; text-transform:uppercase; letter-spacing:.04em;
                     border-bottom:1px solid #e5e7eb; white-space:nowrap; background:#fafafa; }
      .rc-table th.r { text-align:right; }
      .rc-table td { padding:10px 12px; border-bottom:1px solid #f3f4f6;
                     vertical-align:top; color:#111827; }
      .rc-table td.r { text-align:right; font-weight:600; }
      .rc-table td.muted { color:#667085; }
      .rc-table td.reason { font-size:12px; color:#667085; max-width:340px; line-height:1.45; }
      .rc-table tbody tr:hover td { background:#f9fafb; }
      .rc-pager { display:flex; justify-content:flex-end; align-items:center;
                  gap:12px; margin-top:14px; color:#667085; font-size:13px; }
      .rc-btn { padding:6px 14px; border:1px solid #e5e7eb; border-radius:8px;
                background:#fff; color:#374151; font:inherit; font-size:13px; cursor:pointer; }
      .rc-btn:disabled { opacity:.4; cursor:default; }
      .rc-btn:not(:disabled):hover { background:#f3f4f6; }
      @media(max-width:800px){
        .rc-two { grid-template-columns:1fr !important; }
        .rc-kpis { grid-template-columns:1fr 1fr !important; }
      }
    `;
    document.head.appendChild(s);
  }

  // ── page wrapper ─────────────────────────────────────────────────────────
  page.classList.add("rc-page");

  // ── header + filter bar ──────────────────────────────────────────────────
  const head = retailElement("div", undefined, "display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:18px");
  head.className = "dm-page-head";
  const titleBlock = retailElement("div");
  titleBlock.append(
    retailElement("div", "Customer Churn Risk", "", "dm-page-title"),
    retailElement("div",
      "Retail customer early warning — who may stop purchasing, and what observed behavior is behind the score.",
      "", "dm-page-sub")
  );
  const filterBar = retailElement("div", undefined, "", "seg");
  const FILTERS = [["all","All"],["critical","Critical"],["high","High"],["watch","Watch"],["low","Low"]];
  const activeFilter = { value: "all" };
  let pageIndex = 0;

  // ── KPI grid (re-rendered on filter change) ──────────────────────────────
  const kpiGrid = retailElement("div", undefined, "", "rc-kpis");

  const mkKpi = (icon, label, value, sub, valColor) => {
    const node = retailElement("div", undefined, "", "rc-kpi");
    const iconRow = retailElement("div", undefined, "", "rc-kpi-icon");
    iconRow.appendChild(retailElement("i", undefined, "", `ti ${icon}`));
    iconRow.appendChild(retailElement("span", label));
    const val = retailElement("div", value, `color:${valColor || "var(--foreground,#111827)"}`, "rc-kpi-val");
    const subEl = retailElement("div", sub, "", "rc-kpi-sub");
    node.append(iconRow, val, subEl);
    return node;
  };

  // ── risk donut ───────────────────────────────────────────────────────────
  const order = ["CRITICAL","HIGH","WATCH","LOW"];
  const total  = Number(report.total_customers ?? report.total_businesses ?? customers.length);
  let cursor = 0;
  const stops = order.map(k => {
    const s = cursor;
    cursor += total ? (Number(counts[k] || 0) / total) * 100 : 0;
    return `${colors[k]} ${s}% ${cursor}%`;
  }).join(", ");
  const ring = retailElement("div", undefined,
    `width:200px;height:200px;border-radius:50%;background:conic-gradient(${stops || "#e5e7eb 0 100%"});` +
    `flex-shrink:0;position:relative;display:grid;place-items:center`);
  const hole = retailElement("div", undefined,
    "width:140px;height:140px;border-radius:50%;background:var(--card,#fff);" +
    "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px");
  hole.append(
    retailElement("strong", fmtN(total), "font-size:28px;font-weight:700;color:var(--foreground,#111827)"),
    retailElement("span", "Customers scored", "font-size:11px;color:#667085;text-align:center")
  );
  ring.appendChild(hole);
  const legendGrid = retailElement("div", undefined, "", "rc-legend");
  order.forEach(k => {
    const row = retailElement("div", undefined, "", "rc-legend-row");
    const dot = retailElement("span", undefined, `background:${colors[k]}`, "rc-legend-dot");
    const lbl = retailElement("span", k[0] + k.slice(1).toLowerCase());
    const cnt = retailElement("strong", fmtN(counts[k]), `color:${colors[k]}`);
    const pct = retailElement("span", `${total ? Math.round(Number(counts[k]||0)/total*100) : 0}%`, "", "rc-legend-pct");
    row.append(dot, lbl, cnt, pct);
    legendGrid.appendChild(row);
  });
  const donutWrap = retailElement("div", undefined, "", "rc-donut-wrap");
  donutWrap.append(ring, legendGrid);
  const donutCard = retailElement("div", undefined, "", "rc-card");
  const donutTitle = retailElement("div", undefined, "", "rc-card-title");
  donutTitle.append(retailElement("i", undefined, "", "ti ti-chart-donut"), retailElement("span", "Risk tier distribution"));
  donutCard.append(donutTitle, donutWrap);

  // ── feature importance (what drives churn) ───────────────────────────────
  const drivers = report.feature_importance || [];
  const driverCard = retailElement("div", undefined, "", "rc-card");
  const driverTitle = retailElement("div", undefined, "", "rc-card-title");
  driverTitle.append(retailElement("i", undefined, "", "ti ti-list-details"), retailElement("span", "What drives churn risk"));
  const driverSub = retailElement("p", report.scoring_definition || "Configured score weights — model weights, not learned feature importance.",
    "margin:0 0 14px;font-size:12px;color:#667085");
  const driverRows = retailElement("div");
  const maxImp = Math.max(...drivers.map(d => Number(d.importance || 0)), 0.001);
  drivers.forEach(item => {
    const row = retailElement("div", undefined, "", "rc-driver-row");
    const track = retailElement("div", undefined, "", "rc-driver-track");
    const fill  = retailElement("div", undefined,
      `width:${Math.max(0, Math.min(100, Number(item.importance||0)/maxImp*100))}%`, "rc-driver-fill");
    track.appendChild(fill);
    row.append(
      retailElement("span", item.feature),
      track,
      retailElement("span", `${Math.round(Number(item.importance||0)*100)}%`, "", "rc-driver-pct")
    );
    driverRows.appendChild(row);
  });
  if (!drivers.length) driverRows.appendChild(retailElement("p", "No model driver data available.", "color:#667085;font-size:13px"));
  driverCard.append(driverTitle, driverSub, driverRows);

  const chartsRow = retailElement("div", undefined, "", "rc-two");
  chartsRow.append(donutCard, driverCard);

  // ── breakdown bars ───────────────────────────────────────────────────────
  const mkBreakdown = (icon, title, entries, accent) => {
    const c = retailElement("div", undefined, "", "rc-card");
    const hdr = retailElement("div", undefined, "", "rc-card-title");
    hdr.append(retailElement("i", undefined, "", `ti ${icon}`), retailElement("span", title));
    c.appendChild(hdr);
    const maxVal = Math.max(1, ...entries.map(e => Number(e.count || 0)));
    entries.forEach(e => {
      const row = retailElement("div", undefined, "", "rc-bar-row");
      const track = retailElement("div", undefined, "", "rc-bar-track");
      const fill  = retailElement("div", undefined,
        `width:${Number(e.count||0)/maxVal*100}%;background:${accent}`, "rc-bar-fill");
      track.appendChild(fill);
      row.append(retailElement("span", e.name), track,
        retailElement("strong", fmtN(e.count), "text-align:right"));
      c.appendChild(row);
    });
    if (!entries.length) c.appendChild(retailElement("p", "No data available.", "color:#667085;font-size:13px;margin:0"));
    return c;
  };
  const breakdownRow = retailElement("div", undefined, "", "rc-two");
  breakdownRow.append(
    mkBreakdown("ti-building-store", "At-risk customers by product category", report.by_category || [], "#e7000b"),
    mkBreakdown("ti-users",           "At-risk customers by customer segment",  report.by_segment  || [], "#fe9a00")
  );

  // ── ranked table ─────────────────────────────────────────────────────────
  const tableSection = retailElement("div", undefined, "margin-top:4px", "rc-card");
  const tableTitleWrap = retailElement("div", undefined,
    "display:flex;align-items:center;gap:8px;margin-bottom:4px", "rc-card-title");
  tableTitleWrap.append(
    retailElement("i", undefined, "", "ti ti-flame"),
    retailElement("span", "Customers ranked by churn probability (all tiers)")
  );
  tableSection.appendChild(tableTitleWrap);

  const renderTable = () => {
    const filtered = activeFilter.value === "all"
      ? customers
      : customers.filter(r => String(r.risk_tier).toLowerCase() === activeFilter.value);
    const atRisk = filtered.filter(r => ["critical","high"].includes(String(r.risk_tier).toLowerCase())).length;
    const avg    = filtered.length
      ? filtered.reduce((s, r) => s + Number(r.churn_probability || 0), 0) / filtered.length : 0;

    // Update KPIs
    kpiGrid.replaceChildren(
      mkKpi("ti-users",          "Customers scored",
        fmtN(filtered.length),
        activeFilter.value === "all" ? "Unique customers in the scored cohort" : `Filtered to ${activeFilter.value} tier`),
      mkKpi("ti-alert-hexagon",  "At risk (Critical + High)",
        fmtN(atRisk),
        `${filtered.length ? (atRisk/filtered.length*100).toFixed(1) : "0.0"}% of selected`,
        "#d00000"),
      mkKpi("ti-percentage",     "Average churn probability",
        fmtPct(avg),
        "Average of selected customer predictions",
        "#d00000"),
      mkKpi("ti-target-arrow",   "Model confidence",
        report.model_confidence?.quality || "Not evaluated",
        report.model_confidence?.auc == null
          ? "Weighted RFM score · AUC unavailable"
          : `AUC ${Number(report.model_confidence.auc).toFixed(3)}`)
    );

    // Filter button active state
    filterBar.querySelectorAll("button").forEach(b =>
      b.classList.toggle("on", b.dataset.tier === activeFilter.value));

    // Build table
    const pageSize  = 100;
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
    pageIndex = Math.min(pageIndex, pageCount - 1);
    const start = pageIndex * pageSize;
    const rows  = filtered.slice(start, start + pageSize);

    const tbl = retailElement("table", undefined, "", "rc-table");
    // thead
    const colDefs = [
      { label: "No.",             cls: "" },
      { label: "Customer",        cls: "" },
      { label: "Product category",cls: "" },
      { label: "Segment",         cls: "" },
      { label: "Churn prob.",     cls: "r" },
      { label: "Tier",            cls: "" },
      { label: "Likely reason",   cls: "" },
    ];
    const thead = retailElement("thead");
    const thr = retailElement("tr");
    colDefs.forEach(c => {
      const th = retailElement("th", c.label);
      if (c.cls) th.className = c.cls;
      thr.appendChild(th);
    });
    thead.appendChild(thr);
    tbl.appendChild(thead);

    const tbody = retailElement("tbody");
    if (!rows.length) {
      const tr = retailElement("tr");
      const td = retailElement("td", "No customers match this risk tier.", "padding:16px;color:#667085");
      td.colSpan = colDefs.length;
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      rows.forEach((row, i) => {
        const tr    = retailElement("tr");
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => openChurnCustomerModal(row));
        const tier  = String(row.risk_tier || "LOW").toUpperCase();
        const color = colors[tier] || "#555";
        const bg    = tierBg[tier]  || "#f9fafb";

        // Categories: use the array if available, else industry field, else "—"
        const cats = Array.isArray(row.categories) && row.categories.length
          ? row.categories.slice(0, 2).join(", ")
          : (row.industry || "—");

        // # col
        const tdNum = retailElement("td", String(start + i + 1), "", "muted");
        tdNum.style.fontVariantNumeric = "tabular-nums";

        // Customer name col
        const tdName = retailElement("td");
        const nameLink = retailElement("span", row.customer_name || `Customer ${row.customer_id || "—"}`,
          "font-weight:600;color:var(--foreground,#111827);cursor:default");
        tdName.append(nameLink);

        // Category col
        const tdCat = retailElement("td", cats, "font-size:12px;color:#374151");

        // Segment col
        const tdSeg = retailElement("td", row.customer_segment || row.segment || "Unclassified",
          "font-size:12px;color:#374151");

        // Churn prob col
        const tdProb = retailElement("td", fmtPct(row.churn_probability), "", "r");
        tdProb.style.color = color;

        // Tier pill col
        const tdTier = retailElement("td");
        const pill   = retailElement("span", tier, "", "rc-tier-pill");
        pill.style.color = color;
        pill.style.background = bg;
        pill.style.border = `1px solid ${color}44`;
        tdTier.appendChild(pill);

        // Likely reason col
        const reason = row.likely_reason || row.likely_churn_reason || row.top_risk_factors || "—";
        const tdReason = retailElement("td", reason, "", "reason");

        tr.append(tdNum, tdName, tdCat, tdSeg, tdProb, tdTier, tdReason);
        tbody.appendChild(tr);
      });
    }
    tbl.appendChild(tbody);

    // Replace table content inside tableSection (keep title)
    // Remove old table wrapper + pager if present
    tableSection.querySelectorAll(".rc-tbl-wrap, .rc-pager").forEach(n => n.remove());

    const wrap = retailElement("div", undefined, "overflow-x:auto");
    wrap.className = "rc-tbl-wrap";
    wrap.appendChild(tbl);
    tableSection.appendChild(wrap);

    // Pager
    const pager = retailElement("div", undefined, "", "rc-pager");
    const info  = retailElement("span",
      filtered.length
        ? `Showing ${start+1}–${Math.min(start+pageSize, filtered.length)} of ${fmtN(filtered.length)}`
        : "0 customers");
    const prev  = retailElement("button", "← Previous", "", "rc-btn");
    const next  = retailElement("button", "Next →",     "", "rc-btn");
    prev.disabled = pageIndex === 0;
    next.disabled = pageIndex >= pageCount - 1;
    prev.addEventListener("click", () => { pageIndex = Math.max(0, pageIndex - 1); renderTable(); });
    next.addEventListener("click", () => { pageIndex = Math.min(pageCount-1, pageIndex+1); renderTable(); });
    const pageInfo = retailElement("span", `Page ${pageIndex+1} of ${pageCount}`);
    pager.append(info, prev, pageInfo, next);
    tableSection.appendChild(pager);
  };

  // ── wire filter buttons ──────────────────────────────────────────────────
  FILTERS.forEach(([value, label]) => {
    const btn = retailElement("button", label, "", "seg-btn");
    btn.type = "button";
    btn.dataset.tier = value;
    btn.addEventListener("click", () => { activeFilter.value = value; pageIndex = 0; renderTable(); });
    filterBar.appendChild(btn);
  });
  head.append(titleBlock, filterBar);

  // ── assemble page ────────────────────────────────────────────────────────
  page.append(head, kpiGrid, chartsRow, breakdownRow, tableSection);
  renderTable();
}

function patchRetailChurnPage() {
  const isChurn = window.location.hash.includes("/exec/churn");
  if (!isChurn) { restoreRetailChurnPage(); return; }

  const appShell   = document.querySelector(".dm-wrap");
  const appLayout  = appShell?.querySelector(".dm-layout");
  const appSidebar = appLayout?.querySelector(":scope > .dm-sidebar");

  // Guarantee sidebar is never hidden
  if (appSidebar) {
    appSidebar.style.display    = "";
    appSidebar.style.visibility = "";
    delete appSidebar.dataset.retailChurnOriginalDisplay;
  }

  const appMain = appShell?.querySelector(".dm-content") || appShell?.querySelector("main");
  const appRoot = document.getElementById("root");
  const authScreen = appRoot?.querySelector(".auth-screen");
  const host = appMain || (!appShell && appRoot && !authScreen ? appRoot : null);

  if (!host) {
    document.querySelectorAll(".retail-churn-page").forEach(p => {
      const h = p.parentElement;
      p.remove();
      restoreRetailChurnHost(h);
    });
    return;
  }

  document.querySelectorAll(".retail-churn-page").forEach(p => {
    if (p.parentElement === host) return;
    const h = p.parentElement;
    p.remove();
    restoreRetailChurnHost(h);
  });

  const existingPage = host.querySelector(":scope > .retail-churn-page");
  if (existingPage) {
    // Hide React-rendered siblings (Business Churn V6 output) but never the sidebar.
    if (host !== appRoot) hideNativeChurnContent(host, existingPage);
    return;
  }

  const pageStyle = host === appRoot
    ? "width:100%;max-width:1480px;margin:0 auto;padding:20px 22px 30px;box-sizing:border-box"
    : "min-width:0;width:100%";
  const page = retailElement("div", undefined, pageStyle, "retail-churn-page");

  const setLoading = () => {
    const loading = retailElement("div", "Loading retail customer churn results…", "");
    loading.className = "empty-state card";
    page.replaceChildren(loading);
  };
  const showError = message => {
    const panel = retailElement("section", undefined, "");
    panel.className = "card";
    panel.style.cssText = "padding:20px 24px;border:1px solid #e5e7eb;border-radius:14px;background:#fff;box-shadow:0 1px 2px #1018280d";
    panel.appendChild(retailElement("p", message, "margin:0 0 14px;color:#b42318;font-size:15px;font-weight:600"));
    const retry = retailElement("button", "Retry", "padding:9px 16px;border:0;border-radius:8px;background:#2864e8;color:#fff;font:inherit;font-size:14px;font-weight:600;cursor:pointer");
    retry.type = "button";
    retry.addEventListener("click", () => loadReport(true));
    panel.appendChild(retry);
    page.replaceChildren(panel);
  };
  let loading = false;
  const loadReport = async (force = false) => {
    if (force) {
      retailChurnReportCache = null;
      retailChurnLoadError   = null;
      retailChurnRequest     = null;
      try { sessionStorage.removeItem(retailChurnCacheKey); } catch {}
    }
    if (loading || !page.isConnected) return;
    if (retailChurnReportCache) { renderRetailChurnDashboard(host, page, retailChurnReportCache); return; }
    if (retailChurnLoadError)   { showError(retailChurnLoadError); return; }
    loading = true;
    setLoading();
    try {
      const report = await requestRetailChurnReport();
      retailChurnReportCache = report;
      retailChurnLoadError   = null;
      renderRetailChurnDashboard(host, page, report);
    } catch (error) {
      showError(retailChurnLoadError || error?.message || "Retail churn results could not be loaded. Check that the Retail API is running, then retry.");
    } finally {
      loading = false;
    }
  };

  host.appendChild(page);
  if (host !== appRoot) hideNativeChurnContent(host, page);
  loadReport();
}

function retailElement(tag, text, css = "", className = "") {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (css) element.style.cssText = css;
  if (className) element.className = className;
  return element;
}

function formatAlertMonth(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})$/);
  if (!match) return value || "—";
  const month = new Date(Number(match[1]), Number(match[2]) - 1, 1)
    .toLocaleString("en", { month: "long" });
  return `${month} ${match[1]}`;
}

function renderRetailAlerts(host, page, report) {
  if (!page.isConnected) return;
  const alerts = report.alerts || [];
  page.replaceChildren();
  const recipientCard = retailElement("section", undefined,
    "padding:20px 24px;margin-bottom:20px;border:1px solid #e4e4e7;border-radius:16px;background:#fff");
  const emailLabel = retailElement("label", "✉  Recipient email", "display:block;margin-bottom:10px;font-size:13px;font-weight:550;color:#52525b");
  const emailRow = retailElement("div", undefined, "display:flex;gap:12px;align-items:center");
  const emailInput = retailElement("input", undefined,
    "flex:1;min-width:0;height:44px;padding:0 14px;border:1px solid #dedee3;border-radius:10px;font:inherit;font-size:14px;color:#111827;background:#fff");
  emailInput.type = "email";
  emailInput.placeholder = "name@example.com";
  emailInput.setAttribute("aria-label", "Recipient email");
  try { emailInput.value = localStorage.getItem("retail-alert-recipient") || ""; } catch {}
  const status = retailElement("div", "", "margin-top:10px;font-size:13px;color:#64748b;min-height:18px");
  let sendAttempted = false;
  let refreshSentLog = async () => {};
  const emailAll = retailElement("button", `➤  Email all (${alerts.length})`,
    "height:44px;padding:0 18px;border:1px solid #dedee3;border-radius:10px;background:#fff;color:#111827;font:inherit;font-size:14px;cursor:pointer");
  emailLabel.htmlFor = "retail-alert-recipient";
  emailInput.id = "retail-alert-recipient";
  const send = async (button, reportType, alertIndex) => {
    const recipient = emailInput.value.trim();
    if (!recipient) {
      status.textContent = "Enter a recipient email address first.";
      status.style.color = "#b91c1c";
      emailInput.focus();
      return;
    }
    try { localStorage.setItem("retail-alert-recipient", recipient); } catch {}
    sendAttempted = true;
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = "Sending…";
    status.textContent = "Sending the selected retail report…";
    status.style.color = "#64748b";
    try {
      const response = await fetch("/api/alerts/email", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recipient, report_type: reportType, alert_index: alertIndex }),
      });
      const result = await response.json();
      if (!response.ok || !result.sent) throw new Error(result.error || "Email could not be sent.");
      status.textContent = `Email sent to ${recipient}.`;
      status.style.color = "#15803d";
      await refreshSentLog();
    } catch (error) {
      status.textContent = error.message || "Email could not be sent.";
      status.style.color = "#b91c1c";
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  };
  emailAll.type = "button";
  emailAll.addEventListener("click", () => send(emailAll, "predictive", null));
  emailRow.append(emailInput, emailAll);
  recipientCard.append(emailLabel, emailRow, status);
  fetch("/api/alerts/email-config", { cache: "no-store" })
    .then(response => response.ok ? response.json() : null)
    .then(configuration => {
      if (!configuration || sendAttempted || !status.isConnected) return;
      status.textContent = configuration.configured
        ? "SendGrid configuration loaded."
        : configuration.message || "SendGrid email configuration is incomplete.";
      status.style.color = configuration.configured ? "#15803d" : "#b91c1c";
    })
    .catch(() => {});

  const reportCard = retailElement("section", undefined,
    "padding:22px 24px;margin-bottom:20px;border:1px solid #e4e4e7;border-radius:16px;background:#fff");
  reportCard.appendChild(retailElement("h2", "✉  Send a full report", "margin:0 0 8px;font-size:18px;font-weight:650;color:#111827"));
  reportCard.appendChild(retailElement("p", "Email one consolidated report instead of individual alerts. Reports are sent to the recipient above.", "margin:0 0 16px;font-size:14px;line-height:1.5;color:#71717a"));
  const reportGrid = retailElement("div", undefined, "display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px");
  for (const config of [
    { title: "Retail predictive alerts report", text: `A summary email of all ${alerts.length} active retail alerts, including severity, metric, forecast, threshold, and reason.`, kind: "predictive" },
    { title: "Retail anomaly report", text: "A summary of retail data quality checks, flagged records, and anomaly detection results.", kind: "anomaly" },
  ]) {
    const item = retailElement("div", undefined, "padding:18px 20px;border:1px solid #e4e4e7;border-radius:14px;background:#fff");
    item.appendChild(retailElement("h3", `↗  ${config.title}`, "margin:0 0 8px;font-size:15px;font-weight:650;color:#111827"));
    item.appendChild(retailElement("p", config.text, "min-height:42px;margin:0 0 16px;font-size:13.5px;line-height:1.5;color:#71717a"));
    const button = retailElement("button", "✉  Email report", "padding:9px 14px;border:1px solid #dedee3;border-radius:9px;background:#fff;color:#111827;font:inherit;font-size:13px;cursor:pointer");
    button.type = "button";
    button.addEventListener("click", () => send(button, config.kind, null));
    item.appendChild(button);
    reportGrid.appendChild(item);
  }
  reportCard.appendChild(reportGrid);

  const activeCard = retailElement("section", undefined, "padding:22px 24px;border:1px solid #e4e4e7;border-radius:16px;background:#fff");
  activeCard.appendChild(retailElement("h2", `♧  Active predictive alerts (${alerts.length})`, "margin:0 0 8px;font-size:18px;font-weight:650;color:#111827"));
  activeCard.appendChild(retailElement("p", "Each card shows a retail metric forecast that crossed its alert threshold. Select Email to send that alert to the recipient above.", "margin:0 0 16px;font-size:14px;line-height:1.5;color:#71717a"));
  if (!alerts.length) activeCard.appendChild(retailElement("p", "No active predictive alerts for the current retail data.", "color:#71717a;font-size:14px"));
  alerts.forEach((alert, index) => {
    const severity = String(alert.severity || "MEDIUM").toUpperCase();
    const color = severity === "CRITICAL" || severity === "HIGH" ? "#c90016" : severity === "MEDIUM" ? "#a45a00" : "#176b87";
    const item = retailElement("article", undefined, "padding:16px 20px;margin-top:12px;border:1px solid #e4e4e7;border-radius:14px;background:#fff");
    const top = retailElement("div", undefined, "display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;width:100%;min-width:0;box-sizing:border-box");
    const identity = retailElement("div", undefined, "display:flex;align-items:center;gap:10px;flex:1 1 260px;min-width:0;flex-wrap:wrap");
    const badge = retailElement("span", severity, `padding:5px 11px;border:1px solid ${color}33;border-radius:8px;background:${color}10;color:${color};font-size:12px;font-weight:650`);
    const metricName = String(alert.metric || "Retail metric").replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
    identity.append(badge, retailElement("strong", metricName, "min-width:0;white-space:normal;overflow-wrap:anywhere;font-size:15px;line-height:1.4;color:#111827"), retailElement("span", formatAlertMonth(alert.month), "font-size:13px;color:#71717a"));
    const emailButton = retailElement("button", "➤  Send email", "flex:0 0 auto;max-width:100%;padding:9px 13px;border:1px solid #dedee3;border-radius:9px;background:#fff;color:#111827;font:inherit;font-size:13px;cursor:pointer");
    emailButton.type = "button";
    emailButton.addEventListener("click", () => send(emailButton, "predictive", index));
    top.append(identity, emailButton);
    item.append(top);
    item.appendChild(retailElement("div", String(alert.trigger || "Alert").replaceAll("_", " "), "margin-top:8px;font-size:14px;color:#111827"));
    const forecast = Number(alert.forecast);
    const threshold = Number(alert.threshold);
    const gap = Number.isFinite(forecast) && Number.isFinite(threshold) ? forecast - threshold : null;
    const fmt = number => Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: 3 }) : "—";
    const trigger = String(alert.trigger || "").toUpperCase();
    const valueLabel = trigger === "ACTUAL_VS_FORECAST" ? "Deviation" : "Forecast";
    let reason = alert.reason || "The retail metric crossed its configured threshold.";
    const monthName = formatAlertMonth(alert.month);
    if (trigger === "ACTUAL_VS_FORECAST") {
      const direction = forecast >= 0 ? "above" : "below";
      reason = `${monthName.toUpperCase()} actual for ${metricName} differed from the model estimate by ${fmt(Math.abs(forecast))}%, ${direction} the ${fmt(threshold)}% deviation limit.`;
    } else if (trigger === "FORECAST_BELOW_FLOOR") {
      reason = `The ${monthName} forecast for ${metricName} is ${fmt(forecast)}, below its minimum threshold of ${fmt(threshold)} by ${fmt(Math.abs(gap))}.`;
    } else if (trigger === "FORECAST_ABOVE_CEILING") {
      reason = `The ${monthName} forecast for ${metricName} is ${fmt(forecast)}, above its maximum threshold of ${fmt(threshold)} by ${fmt(Math.abs(gap))}.`;
    } else if (trigger === "PILLAR_BELOW_THRESHOLD") {
      reason = `The ${monthName} ${metricName} value (${fmt(forecast)}) is below its ${fmt(threshold)} threshold by ${fmt(Math.abs(gap))}.`;
    } else if (trigger === "ENTITY_TRUST_LOW") {
      reason = `${alert.reason || "Entity trust is below its configured limit."} Current value ${fmt(forecast)} is below the ${fmt(threshold)} threshold by ${fmt(Math.abs(gap))}.`;
    }
    const values = retailElement("div", undefined, "display:flex;gap:18px;flex-wrap:wrap;margin-top:7px;font-size:13px;color:#71717a");
    values.append(retailElement("span", `${valueLabel} ${fmt(forecast)}${trigger === "ACTUAL_VS_FORECAST" ? "%" : ""}`), retailElement("span", `Threshold ${fmt(threshold)}${trigger === "ACTUAL_VS_FORECAST" ? "%" : ""}`), retailElement("span", `Gap ${gap === null ? "—" : `${gap >= 0 ? "+" : ""}${fmt(gap)}${trigger === "ACTUAL_VS_FORECAST" ? "%" : ""}`}`, "font-weight:600;color:#111827"));
    item.appendChild(values);
    item.appendChild(retailElement("p", `Why: ${reason}`, "margin:8px 0 0;font-size:13px;line-height:1.5;color:#71717a"));
    activeCard.appendChild(item);
  });
  const sentLogCard = retailElement("section", undefined,
    "padding:22px 24px;margin-top:20px;border:1px solid #e4e4e7;border-radius:16px;background:#fff");
  const sentLogHeader = retailElement("div", undefined, "display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap");
  sentLogHeader.appendChild(retailElement("h2", "▤  Sent log — when & to whom (0)", "margin:0;font-size:17px;font-weight:650;color:#111827", "retail-sent-log-title"));
  const refreshButton = retailElement("button", "⟳  Refresh", "padding:9px 13px;border:1px solid #dedee3;border-radius:9px;background:#fff;color:#111827;font:inherit;font-size:13px;cursor:pointer");
  refreshButton.type = "button";
  sentLogHeader.appendChild(refreshButton);
  const sentLogEntries = retailElement("div", undefined, "margin-top:16px", "retail-sent-log-entries");
  sentLogCard.append(sentLogHeader, sentLogEntries);
  refreshSentLog = async () => {
    try {
      const response = await fetch("/api/alerts/sent-log", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load sent email history.");
      const result = await response.json();
      const entries = Array.isArray(result.entries) ? result.entries : [];
      sentLogHeader.querySelector(".retail-sent-log-title").textContent = `▤  Sent log — when & to whom (${entries.length})`;
      sentLogEntries.replaceChildren();
      if (!entries.length) {
        sentLogEntries.appendChild(retailElement("p", "No emails sent yet. Send an alert above and it will appear here.", "margin:0;color:#71717a;font-size:13px"));
        return;
      }
      entries.forEach(entry => {
        const row = retailElement("div", undefined, "display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;padding:12px 0;border-top:1px solid #f0f0f2");
        const details = retailElement("div", undefined, "display:grid;gap:4px");
        details.append(
          retailElement("strong", entry.report_label || "Retail alert email", "font-size:13px;color:#111827"),
          retailElement("span", `To: ${entry.recipient || "—"}`, "font-size:13px;color:#52525b"),
          retailElement("span", entry.sent_at ? new Date(entry.sent_at).toLocaleString() : "Time unavailable", "font-size:12px;color:#71717a")
        );
        const countText = entry.report_type === "anomaly" ? "Anomaly report" : `${Number(entry.alert_count) || 0} alert(s)`;
        row.append(details, retailElement("span", countText, "font-size:12px;color:#52525b"));
        sentLogEntries.appendChild(row);
      });
    } catch (error) {
      sentLogEntries.replaceChildren(retailElement("p", error.message || "Could not load sent email history.", "margin:0;color:#b91c1c;font-size:13px"));
    }
  };
  refreshButton.addEventListener("click", refreshSentLog);
  page.append(recipientCard, reportCard, activeCard, sentLogCard);
  refreshSentLog();
}

function restoreRetailAlertsPage() {
  for (const page of document.querySelectorAll(".retail-alert-page")) {
    const host = page.parentElement;
    page.remove();
    if (!host) continue;
    host.querySelectorAll(":scope > .card[data-retail-alert-original-display]").forEach(card => {
      card.style.display = card.dataset.retailAlertOriginalDisplay;
      delete card.dataset.retailAlertOriginalDisplay;
    });
    const subtitle = host.querySelector(".dm-page-head .dm-page-sub");
    if (subtitle && subtitle.dataset.retailAlertOriginalText !== undefined) {
      subtitle.textContent = subtitle.dataset.retailAlertOriginalText;
      delete subtitle.dataset.retailAlertOriginalText;
    }
  }
}
function patchRetailAlertsPage() {
  if (!window.location.hash.includes("/exec/alerts")) { restoreRetailAlertsPage(); return; }
  const pageHead = [...document.querySelectorAll(".dm-page-head")].find(node =>
    node.querySelector(".dm-page-title")?.textContent.trim() === "Alerts");
  const host = pageHead?.parentElement;
  if (!host) return;
  const page = host.querySelector(":scope > .retail-alert-page");
  if (page) return;
  host.querySelectorAll(":scope > .card").forEach(card => {
    if (card.dataset.retailAlertOriginalDisplay === undefined) card.dataset.retailAlertOriginalDisplay = card.style.display;
    card.style.display = "none";
  });
  const subtitle = pageHead.querySelector(".dm-page-sub");
  if (subtitle) {
    if (subtitle.dataset.retailAlertOriginalText === undefined) subtitle.dataset.retailAlertOriginalText = subtitle.textContent;
    subtitle.textContent = "Predictive retail alerts. Email individual alerts or a full report to a recipient.";
  }
  const customPage = retailElement("div", undefined, "display:grid;grid-template-columns:1fr;gap:0;margin-top:18px", "retail-alert-page");
  host.appendChild(customPage);
  fetch("/api/forecast").then(response => {
    if (!response.ok) throw new Error("Could not load retail alerts.");
    return response.json();
  }).then(report => renderRetailAlerts(host, customPage, report))
    .catch(error => {
      if (customPage.isConnected) customPage.replaceChildren(retailElement("div", error.message, "padding:18px;border:1px solid #fecaca;border-radius:12px;color:#b91c1c;background:#fff"));
    });
}

function patchMetricDropdowns() {
  for (const head of document.querySelectorAll(".fc-head")) {
    const title = head.firstElementChild?.textContent?.trim();
    if (!metricTitles.includes(title)) continue;
    if (head.querySelector(".retail-metric-select")) continue;
    const titleNode = head.firstElementChild;
    head.dataset.retailMetric = title;
    const icon = titleNode.querySelector("i");
    [...titleNode.childNodes].forEach(node => { if (node.nodeType === Node.TEXT_NODE) node.remove(); });
    const select = document.createElement("select");
    select.className = "retail-metric-select";
    select.setAttribute("aria-label", "Choose chart metric");
    select.disabled = !metricDataReady;
    select.style.cssText = "max-width:190px;padding:4px 7px;border:1px solid var(--border,#d1d5db);border-radius:6px;background:var(--card,#fff);color:var(--foreground,#111827);font:inherit;font-size:13px;cursor:pointer";
    for (const metric of metricTitles) {
      const option = document.createElement("option");
      option.value = metric;
      option.textContent = metric;
      select.appendChild(option);
    }
    select.value = title;
    if (icon) titleNode.insertBefore(select, icon.nextSibling);
    else titleNode.appendChild(select);
    select.addEventListener("change", () => {
      const previousMetric = head.dataset.retailMetric;
      const nextMetric = select.value;
      const otherHead = [...document.querySelectorAll(".fc-head[data-retail-metric]")]
        .find(candidate => candidate !== head && candidate.querySelector(".retail-metric-select")?.value === nextMetric);
      const otherSelect = otherHead?.querySelector(".retail-metric-select");
      if (!applyMetric(head.closest(".card"), nextMetric)) {
        select.value = previousMetric;
        return;
      }
      head.dataset.retailMetric = nextMetric;
      if (otherHead && otherSelect && applyMetric(otherHead.closest(".card"), previousMetric)) {
        otherSelect.value = previousMetric;
        otherHead.dataset.retailMetric = previousMetric;
      }
    });
    head.dataset.retailMetric = title;
  }
  registerMetricCharts();
  refreshMetricChoices();
  removeMetricForecastBadges();
  loadAdditionalMetrics();
  for (const head of document.querySelectorAll(".fc-head[data-retail-metric]")) {
    const card = head.closest(".card");
    if (!card || card.dataset.metricObserverReady) continue;
    card.dataset.metricObserverReady = "true";
    const observer = new MutationObserver(() => {
      registerMetricCharts();
      const select = head.querySelector(".retail-metric-select");
      if (select && applyMetric(card, select.value)) observer.disconnect();
    });
    observer.observe(card, { childList: true, subtree: true });
    requestAnimationFrame(registerMetricCharts);
  }
}

function patchBusinessTableRows() {
  if (!window.location.hash.includes("/engine/anomaly")) return;
  for (const card of document.querySelectorAll(".card")) {
    const heading = card.querySelector(".section-title");
    if (!heading?.textContent.toLowerCase().includes("worst offenders")) continue;
    const table = card.querySelector("table");
    if (!table || table.dataset.businessPopupBound) continue;
    table.dataset.businessPopupBound = "true";
    table.style.cursor = "pointer";
    table.addEventListener("mouseover", event => {
      const row = event.target.closest("tbody tr");
      if (row) row.style.background = "color-mix(in srgb, var(--accent,#f1f5f9) 60%, transparent)";
    });
    table.addEventListener("mouseout", event => {
      const row = event.target.closest("tbody tr");
      if (row) row.style.background = "";
    });
    table.addEventListener("click", event => {
      const row = event.target.closest("tbody tr");
      if (!row || !table.contains(row)) return;
      // First cell is Record "#XXXXX", second is Business ID
      const cells = [...row.querySelectorAll("td")];
      if (cells.length < 2) return;
      const rawId = cells[0]?.textContent?.trim().replace(/^#/, "");
      if (!rawId || rawId === "—") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      openRetailRecordDetails(rawId, null);
    }, true);
    table.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tbody tr");
      if (!row) return;
      const cells = [...row.querySelectorAll("td")];
      const rawId = cells[0]?.textContent?.trim().replace(/^#/, "");
      if (rawId && rawId !== "—") openRetailRecordDetails(rawId, null);
    });
    table.querySelectorAll("tbody tr").forEach(row => {
      row.setAttribute("tabindex", "0");
      row.setAttribute("role", "button");
    });
  }
}

function patchAnomalyTableRows() {
  if (!window.location.hash.includes("/engine/anomaly")) return;
  for (const card of document.querySelectorAll(".card")) {
    const heading = card.querySelector(".section-title");
    if (!heading?.textContent.toLowerCase().includes("most anomalous")) continue;
    const table = card.querySelector("table");
    if (!table || table.dataset.anomalyPopupBound) continue;
    table.dataset.anomalyPopupBound = "true";
    table.style.cursor = "pointer";
    // Highlight rows on hover
    table.addEventListener("mouseover", event => {
      const row = event.target.closest("tbody tr");
      if (row) row.style.background = "color-mix(in srgb, var(--accent,#f1f5f9) 60%, transparent)";
    });
    table.addEventListener("mouseout", event => {
      const row = event.target.closest("tbody tr");
      if (row) row.style.background = "";
    });
    table.addEventListener("click", event => {
      const row = event.target.closest("tbody tr");
      if (!row || !table.contains(row)) return;
      // The 2nd cell contains the record id as "#10747"
      const cells = [...row.querySelectorAll("td")];
      if (cells.length < 2) return;
      const rawId = cells[1]?.textContent?.trim().replace(/^#/, "");
      if (!rawId) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      openRetailRecordDetails(rawId, null);
    }, true);
    table.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tbody tr");
      if (!row) return;
      const cells = [...row.querySelectorAll("td")];
      const rawId = cells[1]?.textContent?.trim().replace(/^#/, "");
      if (rawId) openRetailRecordDetails(rawId, null);
    });
    // Make rows focusable for keyboard nav
    table.querySelectorAll("tbody tr").forEach(row => {
      row.setAttribute("tabindex", "0");
      row.setAttribute("role", "button");
      row.setAttribute("aria-label", "View record details");
    });
  }
}

function patchHyperparamsPage() {
  if (!window.location.hash.includes("/engine/hyperparams")) return;

  // Find the sidebar panel that has Save Config and Reset buttons
  const saveBtn = [...document.querySelectorAll("button")].find(
    b => b.textContent.trim() === "Save Config" || b.textContent.includes("Save Config")
  );
  if (!saveBtn) return;

  const panel = saveBtn.closest("div");
  if (!panel) return;
  panel.style.display = "flex";
  panel.style.flexDirection = "column";
  panel.style.gap = "8px";

  const configName = panel.querySelector('input[placeholder="Config name (optional)"]');
  if (configName) configName.style.marginBottom = "0";
  saveBtn.style.marginBottom = "0";
  saveBtn.style.minHeight = "42px";
  const existingReset = [...panel.querySelectorAll("button")].find(button => button.textContent.includes("Reset all"));
  if (existingReset) {
    existingReset.style.margin = "0";
    existingReset.style.minHeight = "42px";
  }
  if (panel.dataset.rerunBtnAdded) return;
  panel.dataset.rerunBtnAdded = "true";

  // Hide the React success toast (#ecfdf3 background) — only success, keep error visible.
  if (!panel.dataset.toastObserverAdded) {
    panel.dataset.toastObserverAdded = "true";
    new MutationObserver(() => {
      panel.querySelectorAll("div").forEach(el => {
        if (el.style.background === "rgb(236, 253, 243)" ||
            el.style.background === "#ecfdf3") {
          el.style.display = "none";
        }
      });
    }).observe(panel, { childList: true, subtree: true, attributes: true, attributeFilter: ["style"] });
  }

  // Insert "Save & Re-run Pipeline" button after Save Config
  const rerunBtn = document.createElement("button");
  rerunBtn.type = "button";
  rerunBtn.style.cssText =
    "display:flex;align-items:center;justify-content:center;gap:7px;width:100%;" +
    "padding:9px 12px;border:1px solid var(--border,#e5e7eb);border-radius:8px;" +
    "background:var(--card,#fff);color:var(--foreground,#111827);font:inherit;" +
    "font-size:13.5px;cursor:pointer;margin:0;min-height:42px;box-sizing:border-box;";
  rerunBtn.innerHTML =
    '<i class="ti ti-player-play" style="font-size:13px"></i> Save &amp; Re-run Pipeline';

  rerunBtn.addEventListener("mouseenter", () => {
    rerunBtn.style.background = "var(--accent,#f1f5f9)";
  });
  rerunBtn.addEventListener("mouseleave", () => {
    rerunBtn.style.background = "var(--card,#fff)";
  });

  rerunBtn.addEventListener("click", async () => {
    saveBtn.click();
    await new Promise(r => setTimeout(r, 600));
    let msgEl = panel.querySelector(".rerun-msg");
    if (!msgEl) {
      msgEl = document.createElement("div");
      msgEl.className = "rerun-msg";
      msgEl.style.cssText =
        "margin-top:10px;padding:8px 10px;border-radius:8px;font-size:12px;" +
        "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;line-height:1.5";
      panel.appendChild(msgEl);
    }
    msgEl.innerHTML =
      '<i class="ti ti-circle-check" style="margin-right:5px"></i>' +
      'Config saved. Run <code style="background:#dbeafe;padding:1px 5px;border-radius:4px">python 03_Trust_Score.py</code> to apply.';
    setTimeout(() => { if (msgEl.parentNode) msgEl.remove(); }, 6000);
  });

  const resetBtn = [...panel.querySelectorAll("button")].find(
    b => b.textContent.includes("Reset all")
  );
  if (resetBtn) {
    resetBtn.style.margin = "0";
    resetBtn.style.minHeight = "42px";
    panel.insertBefore(rerunBtn, resetBtn);
  } else {
    panel.appendChild(rerunBtn);
  }
}

const assistantLiveCache = new Map();
async function assistantLiveData(url) {
  const cached = assistantLiveCache.get(url);
  if (cached && Date.now() - cached.time < 20000) return cached.value;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Dashboard data request failed (${response.status}).`);
  const value = await response.json();
  assistantLiveCache.set(url, { time: Date.now(), value });
  return value;
}

function assistantNumber(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: digits }) : "not available";
}

async function answerFromDashboard(question, scope) {
  const q = question.toLowerCase();
  const asks = pattern => pattern.test(q);
  if (scope === "Cloud Migration" || asks(/migration|oracle|bigquery|discard|lineage|migrat/)) {
    const m = await assistantLiveData("/api/migration");
    if (!m.ready) return m.hint || "Migration results are not available yet.";
    if (asks(/discard|clean|removed|anomal/)) {
      const d = await assistantLiveData("/api/migration/discarded");
      const reasons = new Map();
      for (const row of d.records || []) reasons.set(row.reason || "Unspecified", (reasons.get(row.reason || "Unspecified") || 0) + 1);
      const topReasons = [...reasons].sort((a, b) => b[1] - a[1]).slice(0, 5);
      return `${assistantNumber(d.total, 0)} records were discarded during cleaning. Common reasons: ${topReasons.length ? topReasons.map(([name, count]) => `${name.replaceAll("_", " ")} (${count})`).join(", ") : "not recorded"}.`;
    }
    if (asks(/lineage|lift|improv/)) {
      const rows = [...(m.lineage_lift || [])].sort((a, b) => Number(b.lift) - Number(a.lift));
      return rows.length ? `Largest lineage trust lift: ${rows.slice(0, 5).map(row => `${row.lineage} +${assistantNumber(row.lift)} (${assistantNumber(row.before)} → ${assistantNumber(row.after)})`).join("; ")}.` : `Migration trust changed by ${assistantNumber(m.trust_delta)} points.`;
    }
    return `Migration status: ${m.status}. Trust moved from ${assistantNumber(m.trust_before)} to ${assistantNumber(m.trust_after)} (${Number(m.trust_delta) >= 0 ? "+" : ""}${assistantNumber(m.trust_delta)} points). ${assistantNumber(m.records_migrated, 0)} records migrated and ${assistantNumber(m.records_discarded, 0)} discarded; ${assistantNumber(m.retained_pct)}% retained.`;
  }
  if (scope === "MDM" || asks(/\bmdm\b|golden record|duplicate|match confidence|source system/)) {
    const m = await assistantLiveData("/api/mdm");
    if (!m.ready) return m.hint || "MDM results are not available yet.";
    const x = m.metrics || {};
    if (asks(/duplicate|cluster/)) return `MDM has ${assistantNumber(x.duplicate_clusters, 0)} duplicate clusters among ${assistantNumber(x.source_records, 0)} source records.`;
    if (asks(/source|system|win|golden.*most/)) {
      const winners = [...(m.pipeline || [])].sort((a, b) => Number(b.golden_won) - Number(a.golden_won));
      return winners.length ? `Top source for golden-record wins: ${winners.slice(0, 5).map(row => `${row.source}: ${assistantNumber(row.golden_won, 0)} (${assistantNumber(row.win_pct)}%)`).join("; ")}.` : "Source-system win-rate data is not available.";
    }
    if (asks(/review|lowest|worst|record/)) return m.review?.length ? `Records to review: ${m.review.slice(0, 8).map(row => `${row.cluster_id} ${row.name} (trust ${row.trust}, source ${row.source})`).join("; ")}.` : "There are no MDM review records in the latest result.";
    if (asks(/pillar|breakdown|rules|drift|anomal/)) return `MDM trust is ${assistantNumber(m.overall_trust)}/100. Pillars: ${Object.entries(m.pillars || {}).map(([name, value]) => `${name} ${assistantNumber(value)}`).join(", ")}.`;
    return `MDM summary: ${assistantNumber(x.source_records, 0)} source records, ${assistantNumber(x.golden_records, 0)} golden records, ${assistantNumber(x.duplicate_clusters, 0)} duplicate clusters, ${assistantNumber(x.match_confidence)}% match confidence.`;
  }
  if (scope === "Churn Risk" || asks(/churn|customer|purchase|inactive|retention/)) {
    const c = retailChurnReportCache || await assistantLiveData("/api/churn-risk?limit=10000&format=dashboard");
    if (!c.ready) return c.hint || "Retail customer churn results are not available yet.";
    const customers = c.customers || [];
    const match = q.match(/cust\d+/i)?.[0];
    if (match) {
      const row = customers.find(item => String(item.customer_id).toLowerCase() === match.toLowerCase());
      return row ? `${row.customer_name} has ${assistantNumber(row.churn_probability)}% churn probability (${row.risk_tier}). ${row.likely_reason || "No specific reason is recorded."}` : `I couldn't find ${match} in the current churn results.`;
    }
    const tierCounts = c.tier_counts || {};
    const ranked = customers.slice().sort((a, b) => Number(b.churn_probability) - Number(a.churn_probability));
    if (asks(/highest|top|most likely|at risk|who|which customer/)) return ranked.length ? `Highest churn-risk customers: ${ranked.slice(0, 8).map(row => `${row.customer_id} ${assistantNumber(row.churn_probability)}% (${row.risk_tier})`).join("; ")}.` : "No customer churn rows are available.";
    if (asks(/why|reason/)) return ranked.length ? `Common high-risk signals include: ${ranked.slice(0, 5).map(row => `${row.customer_id}: ${row.likely_reason}`).join("; ")}.` : "No churn reasons are available.";
    if (asks(/critical|high|watch|low|tier|distribution/)) return `Customer risk tiers: ${Object.entries(tierCounts).map(([name, count]) => `${name}: ${assistantNumber(count, 0)}`).join(", ")}.`;
    return `Churn summary: ${assistantNumber(c.total_customers, 0)} customers scored, ${assistantNumber(c.at_risk_count, 0)} at critical/high risk, with an average churn probability of ${assistantNumber(c.avg_churn_probability)}%.`;
  }
  if (asks(/fresh|late|arrival|delivery|days late/)) {
    const f = await assistantLiveData("/api/engine/freshness");
    if (!f.ready) return f.hint || "Freshness data is not available yet.";
    if (asks(/month|trend|over time/)) return `Average days late by month: ${(f.monthly || []).map(row => `${row.month}: ${assistantNumber(row.avg_days_late)}d`).join("; ")}.`;
    if (asks(/record|worst|most late/)) return f.worst_records?.length ? `Most delayed records: ${f.worst_records.slice(0, 8).map(row => `${row.businessID} ${assistantNumber(row.days_late)}d (${row.data_month})`).join("; ")}.` : "No delayed records are listed.";
    return `Freshness: ${assistantNumber(f.avg_days_late)} average days late, ${assistantNumber(f.late_rate)}% late records, ${assistantNumber(f.fresh_pct)}% freshness health.`;
  }
  if (asks(/drift|stable|lineage/)) {
    const d = await assistantLiveData("/api/engine/drift");
    if (!d.ready) return d.hint || "Drift results are not available yet.";
    return `Drift health by lineage: ${(d.lineage_drift || []).slice(0, 8).map(row => `${row.lineage}: ${assistantNumber(row.drift_health)} health`).join("; ")}.`;
  }
  if (asks(/business rule|rule violation|violations/)) {
    const b = await assistantLiveData("/api/engine/business");
    if (!b.ready) return b.hint || "Business-rule results are not available yet.";
    return `Business-rule severity: ${(b.severity_dist || []).map(row => `${row.label}: ${assistantNumber(row.count, 0)}`).join("; ")}.`;
  }
  if (asks(/alert|urgent|warning/)) {
    const r = await assistantLiveData("/api/forecast");
    return r.alerts?.length ? `There are ${r.alerts.length} predictive alerts. ${r.alerts.slice(0, 8).map(row => `${row.severity}: ${row.metric} (${row.month}) — ${row.trigger}`).join("; ")}.` : "There are no predictive alerts in the latest forecast.";
  }
  if (asks(/forecast|predict|next month|future/)) {
    const r = await assistantLiveData("/api/forecast");
    const s = (r.series || []).find(row => row.is_overall || /overall.*trust/i.test(row.label || ""));
    return s?.forecast_values?.length ? `Overall trust forecast: ${s.forecast_labels.map((month, index) => `${month}: ${assistantNumber(s.forecast_values[index])}`).join("; ")}. The current retail forecast contains ${s.forecast_values.length} months.` : "There is no overall-trust forecast in the latest run.";
  }
  if (asks(/anomal|affected|flagged|worst record|quality risk/)) {
    const a = await assistantLiveData("/api/engine/anomaly");
    if (!a.ready) return a.hint || "Anomaly results are not available yet.";
    if (asks(/worst|which record|highest risk/)) return a.worst_records?.length ? `Highest anomaly-risk records: ${a.worst_records.slice(0, 8).map(row => `${row.businessID} (trust ${assistantNumber(row.trust)}, risk ${assistantNumber(row.risk)}%)`).join("; ")}.` : "No anomaly-risk records are listed.";
    return `${assistantNumber(a.flagged_records, 0)} records have anomaly signals (${assistantNumber(a.flag_rate)}% of ${assistantNumber(a.total_records, 0)} records); ${assistantNumber(a.clean_records, 0)} are clean.`;
  }
  const [home, anomaly] = await Promise.all([
    assistantLiveData("/api/home"), assistantLiveData("/api/engine/anomaly"),
  ]);
  if (!home.ready) return "Live dashboard data is not available right now.";
  const metrics = home.metrics || {};
  const pillars = Object.entries(home.pillars || {}).sort((a, b) => Number(a[1]) - Number(b[1]));
  const weak = pillars[0];
  if (asks(/prior|previous|historical.*average|past months/) && metrics.prev_months_avg_trust != null) {
    return `Average trust across the prior ${assistantNumber(metrics.prev_months_count, 0)} months was ${assistantNumber(metrics.prev_months_avg_trust)}. Months: ${(metrics.prev_months_range || "").replaceAll(", ", ", ")}.`;
  }
  if (asks(/trend|changed|change over time|rise|fall|drop|increase|decrease/)) {
    const points = (home.trend?.actual || []).map((value, index) => value == null ? null : `${home.trend.labels?.[index]}: ${assistantNumber(value)}`).filter(Boolean);
    return points.length ? `Monthly trust trend: ${points.join("; ")}.` : "The dashboard has no monthly trust history available.";
  }
  if (asks(/\b(total|how many|number of)\b.*\b(records|rows|customers)\b/)) {
    if (asks(/this month|current month|latest month/)) return `There are ${assistantNumber(metrics.current_month_records, 0)} records in ${metrics.current_month_label || "the current month"}.`;
    return `The latest scored dataset contains ${assistantNumber(anomaly.total_records, 0)} records.`;
  }
  if (asks(/sales|revenue|units sold|inventory|stock remaining/)) {
    const forecast = await assistantLiveData("/api/forecast");
    const series = (forecast.series || []).find(row => {
      const label = String(row.label || row.key || "").toLowerCase();
      return asks(/sales|revenue/) ? /sales|revenue/.test(label) : asks(/units sold/) ? /units sold/.test(label) : /inventory|stock/.test(label);
    });
    if (series) {
      const last = series.hist_values?.length - 1;
      const next = series.forecast_values?.[0];
      return `${series.label}: latest actual ${assistantNumber(series.hist_values?.[last])} (${series.hist_labels?.[last] || "latest month"})${next == null ? "." : `; next forecast ${assistantNumber(next)} (${series.forecast_labels?.[0]}).`}`;
    }
  }
  if (asks(/trust|score|doing|summary|overview|status/)) return `Current retail trust is ${assistantNumber(metrics.overall_trust)}/100 for ${metrics.current_month_label || "the latest month"}. ${weak ? `Weakest pillar: ${weak[0].replaceAll("_", " ")} at ${assistantNumber(weak[1])}.` : ""} The latest batch has ${assistantNumber(metrics.current_month_records, 0)} records and ${assistantNumber(metrics.current_month_flagged, 0)} flagged records.`;
  return `I checked the live dashboard. Current trust is ${assistantNumber(metrics.overall_trust)}/100; ${assistantNumber(anomaly.flagged_records, 0)} records have anomaly signals; average freshness delay is available under Core Engine → Freshness. Ask about trust, anomalies, freshness, forecasts, churn, MDM, or migration, and I’ll use the latest dashboard results.`;
}

function bindDashboardAssistant() {
  const run = (panel, question) => {
    if (!panel || !question) return;
    const requestId = String((Number(panel.dataset.liveAnswerRequest) || 0) + 1);
    panel.dataset.liveAnswerRequest = requestId;
    window.setTimeout(async () => {
      const thread = panel.querySelector(".agent-thread");
      const bot = thread?.querySelector(".agent-msg.bot:last-child");
      if (!bot) return;
      bot.textContent = "Checking the latest dashboard data…";
      const scope = panel.querySelector(".agent-switcher-btn.active")?.textContent.trim() || "Data Intelligence";
      try {
        const answer = await answerFromDashboard(question, scope);
        if (panel.dataset.liveAnswerRequest === requestId && bot.isConnected) bot.textContent = answer;
      } catch (error) {
        if (panel.dataset.liveAnswerRequest === requestId && bot.isConnected) {
          bot.textContent = `I couldn’t retrieve the latest dashboard data: ${error.message}`;
        }
      }
    }, 0);
  };
  document.addEventListener("click", event => {
    const button = event.target.closest?.(".dm-agent .agent-send, .dm-agent .agent-chip");
    const panel = button?.closest(".dm-agent");
    const question = button?.classList.contains("agent-chip")
      ? button.textContent.trim()
      : panel?.querySelector(".dm-agent-foot textarea")?.value.trim();
    if (panel && question) run(panel, question);
  }, true);
  document.addEventListener("keydown", event => {
    const input = event.target.closest?.(".dm-agent .dm-agent-foot textarea");
    if (!input || event.key !== "Enter" || event.shiftKey) return;
    const panel = input.closest(".dm-agent");
    if (panel && input.value.trim()) run(panel, input.value.trim());
  }, true);
  // Use the same live dashboard answer path for the dedicated agent chat.
  if (!window.__dmAgentAnswerFetchPatched) {
    window.__dmAgentAnswerFetchPatched = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const rawUrl = typeof input === "string" || input instanceof URL ? String(input) : input?.url || "";
      const method = String(init.method || input?.method || "GET").toUpperCase();
      let pathname = "";
      try { pathname = new URL(rawUrl, window.location.href).pathname; } catch {}
      if (pathname === "/api/agent" && method === "POST") {
        try {
          const payload = JSON.parse(init.body || "{}");
          const scope = ({mdm: "MDM", migration: "Cloud Migration", churn: "Churn Risk", engine: "Core Engine"})[payload.agent] || "Data Intelligence";
          const answer = await answerFromDashboard(String(payload.question || ""), scope);
          return new Response(JSON.stringify({ready: true, answer}), {status: 200, headers: {"Content-Type": "application/json"}});
        } catch (error) {
          return new Response(JSON.stringify({ready: false, error: `Could not read live dashboard data: ${error.message}`}), {status: 200, headers: {"Content-Type": "application/json"}});
        }
      }
      return nativeFetch(input, init);
    };
  }
}
bindDashboardAssistant();

function patchRetailModal() {
  if (!document.getElementById("dm-hide-agent-bigquery-note")) {
    const style = document.createElement("style");
    style.id = "dm-hide-agent-bigquery-note";
    style.textContent = ".ap .agent-foot-note { display: none !important; }";
    document.head.appendChild(style);
  }
  patchRetailArimaChart();
  patchRetailAlertsPage();
  patchRetailChurnPage();
  patchMetricDropdowns();
  patchPredictiveAlertRows();
  patchAffectedRecordRows();
  patchAnomalyTableRows();
  patchBusinessTableRows();
  patchHyperparamsPage();
  hideMonthOverMonthSection();
  for (const paragraph of document.querySelectorAll("p")) {
    if (paragraph.textContent.includes("creditScore, totalCreditLimit")) {
      paragraph.textContent = "Forecasts for retail fields such as product sales, total sales, inventory levels, and freshness. Changes in these measures help explain movement in the trust pillars above.";
    }
  }
  const modal = document.querySelector(".record-modal-content");
  if (!modal) return;
  const details = modal.textContent || "";
  for (const [oldLabel, newLabel, getValue] of fieldMap) {
    const label = [...modal.querySelectorAll("div")].find(node => node.textContent.trim() === oldLabel);
    if (!label?.parentElement) continue;
    label.textContent = newLabel;
    const value = label.parentElement.children[1];
    if (value) value.textContent = getValue(details);
  }
}

window.addEventListener("hashchange", patchRetailModal);
window.addEventListener("popstate", patchRetailModal);

let retailModalPatchScheduled = false;
new MutationObserver(records => {
  const relevant = records.some(record => {
    if (record.target.closest?.(".retail-alert-modal-backdrop, .retail-churn-page")) return false;
    const changed = [...record.addedNodes, ...record.removedNodes];
    return !changed.length || changed.some(node => node.nodeType !== Node.ELEMENT_NODE ||
      (!node.matches(".retail-alert-modal-backdrop, .retail-churn-page") &&
        !node.closest(".retail-alert-modal-backdrop, .retail-churn-page")));
  });
  if (relevant && !retailModalPatchScheduled) {
    retailModalPatchScheduled = true;
    requestAnimationFrame(() => {
      retailModalPatchScheduled = false;
      patchRetailModal();
    });
  }
}).observe(document.body, { childList: true, subtree: true });
patchRetailModal();
restoreAlertReturnPosition();


// ============================================================



// ============================================================
// AZURE → GCP MIGRATION MODULE
// Matches the reference UI exactly. Powered by real retail data.
// ============================================================
(function initMigrationPage() {

  // ── Inject styles ──────────────────────────────────────────────────────
  if (!document.getElementById("mig-style")) {
    const st = document.createElement("style");
    st.id = "mig-style";
    st.textContent = `
      .mig-page { min-width:0; }
      .mig-top-bar {
        display:flex; align-items:center; justify-content:space-between;
        flex-wrap:wrap; gap:10px; margin-bottom:14px;
      }
      .mig-run-row { display:flex; justify-content:flex-end; margin:0 0 10px; }
      .mig-overview-topbar { flex-wrap:nowrap; padding:16px 24px; background:var(--card,#fff); border:1px solid var(--border,#e5e7eb); border-radius:14px; margin-bottom:16px; }
      .mig-run-row .mig-run-btn { border-color:#d1e9e3; color:#008a70; }
      .mig-cloud-route {
        display:flex; align-items:center; gap:8px;
      }
      .mig-cloud-badge {
        padding:5px 14px; border-radius:6px; font-size:12px; font-weight:600;
        border:1px solid;
      }
      .mig-cloud-badge.azure { background:#e0f0ff; color:#0078d4; border-color:#0078d4; }
      .mig-cloud-badge.gcp   { background:#e6f4ea; color:#1a73e8; border-color:#1a73e8; }
      .mig-arrow-icon { color:#6b7280; font-size:16px; }
      .mig-steps {
        display:flex; align-items:center; gap:6px; flex-wrap:wrap;
      }
      .mig-step-btn {
        display:flex; align-items:center; gap:6px;
        padding:5px 12px; border-radius:6px; font-size:12px; font-weight:500;
        border:1px solid #bbf7d0; background:#f0fdf4; color:#008a70; cursor:default;
      }
      .mig-step-btn i { font-size:13px; color:#059669; }
      .mig-kpis {
        display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
        gap:14px; margin-bottom:16px;
      }
      .mig-kpi {
        background:var(--card,#fff); border:1px solid var(--border,#e5e7eb);
        border-radius:14px; padding:20px 22px;
      }
      .mig-kpi-icon-row {
        display:flex; align-items:center; gap:7px;
        font-size:12px; color:#9ca3af; margin-bottom:8px;
      }
      .mig-kpi-icon-row i { font-size:15px; }
      .mig-kpi-val  { font-size:34px; font-weight:700; line-height:1.1; margin-bottom:7px; }
      .mig-kpi-sub  { font-size:12px; color:#9ca3af; }
      .mig-two-col  {
        display:grid; grid-template-columns:1fr 1fr;
        gap:16px; margin-bottom:16px;
      }
      .mig-card {
        background:var(--card,#fff); border:1px solid var(--border,#e5e7eb);
        border-radius:12px; padding:18px 20px; margin-bottom:16px;
      }
      .mig-card-title {
        display:flex; align-items:center; gap:8px;
        font-size:14px; font-weight:650; margin-bottom:14px;
        color:var(--foreground,#111827);
      }
      .mig-overview-card { padding:22px 24px; }
      .mig-overview-card .mig-card-title { font-size:18px; margin-bottom:18px; }
      .mig-overview-card .mig-card-title > i:first-child { color:#009688; }
      .mig-overview-card .mig-pillar-val { color:#169968 !important; }
      .mig-before-after-chart-card { padding:20px 24px 26px; }
      .mig-before-after-chart-card .mig-card-title { font-size:18px; margin-bottom:18px; }
      .mig-discarded-head { display:flex; align-items:center; gap:12px; margin-bottom:18px; }
      .mig-discarded-count { padding:4px 12px; border:1px solid #fda4af; border-radius:7px; color:#b91c1c; background:#fff1f2; font-size:14px; font-weight:700; }
      .mig-discarded-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; margin-bottom:22px; }
      .mig-discarded-kpi { min-width:0; background:var(--card,#fff); border:1px solid var(--border,#e5e7eb); border-radius:14px; padding:20px 22px; }
      .mig-discarded-kpi-label { display:flex; align-items:center; gap:8px; color:#64748b; font-size:14px; margin-bottom:12px; }
      .mig-discarded-kpi-value { font-size:34px; line-height:1; font-weight:700; }
      .mig-discarded-section { padding:22px 24px; margin-bottom:20px; }
      .mig-discarded-section .mig-card-title { font-size:18px; margin-bottom:26px; }
      .mig-risk-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:36px; }
      .mig-risk-label { display:block; padding:4px 11px; border:1px solid; border-radius:7px; font-size:12px; font-weight:600; }
      .mig-risk-count { margin-top:14px; font-size:28px; line-height:1; font-weight:700; }
      .mig-risk-share { margin-top:8px; color:#64748b; font-size:12px; }
      .mig-risk-track { height:4px; margin-top:12px; border-radius:4px; background:#f1f5f9; overflow:hidden; }
      .mig-risk-fill { height:100%; min-width:0; border-radius:4px; }
      .mig-discarded-filters { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin:-8px 0 18px; }
      .mig-discarded-filter { padding:5px 12px; border:0; border-radius:7px; background:transparent; color:#111827; font:inherit; font-size:13px; cursor:pointer; }
      .mig-discarded-filter.active { background:#2563eb; color:#fff; font-weight:600; }
      .mig-discarded-filter-label { color:#64748b; font-size:12px; }
      .mig-discarded-table { min-width:900px; }
      .mig-discarded-table th { padding:10px 12px; text-transform:none; letter-spacing:0; font-size:13px; font-weight:500; color:#64748b; white-space:nowrap; }
      .mig-discarded-table th button { padding:0; border:0; background:none; color:inherit; font:inherit; cursor:pointer; }
      .mig-discarded-table td { padding:12px; color:#111827; font-size:13px; white-space:nowrap; }
      .mig-discarded-table td.muted { color:#64748b; }
      .mig-discarded-lineage { display:inline-block; padding:4px 10px; border:1px solid #e5e7eb; border-radius:8px; background:#f8fafc; color:#64748b; font-size:12px; }
      .mig-discarded-risk-pill { display:inline-block; padding:4px 12px; border:1px solid; border-radius:8px; font-size:12px; font-weight:600; }
      .mig-discarded-footer { display:flex; align-items:center; justify-content:space-between; gap:16px; padding-top:18px; color:#64748b; font-size:13px; }
      .mig-discarded-pages { display:flex; align-items:center; gap:8px; color:#111827; }
      .mig-discarded-pages button { min-width:42px; height:38px; padding:0 12px; border:1px solid #e5e7eb; border-radius:10px; background:#fff; color:inherit; cursor:pointer; }
      .mig-discarded-pages button:disabled { color:#9ca3af; cursor:default; }
      .mig-donut-wrap {
        display:flex; align-items:center; gap:44px;
      }
      .mig-donut-ring {
        position:relative; width:260px; height:260px; flex:0 0 260px;
      }
      .mig-donut-center {
        position:absolute; inset:0;
        display:flex; flex-direction:column; align-items:center; justify-content:center;
      }
      .mig-donut-val  { font-size:50px; font-weight:700; color:#169968; }
      .mig-donut-lbl  { font-size:13px; color:#6b7280; }
      .mig-pillar-list { flex:1; display:flex; flex-direction:column; gap:24px; }
      .mig-pillar-row  {
        display:flex; align-items:center; justify-content:space-between;
        font-size:15px; color:#111827;
      }
      .mig-pillar-left { display:flex; align-items:center; gap:8px; }
      .mig-pillar-dot  { width:11px; height:11px; border-radius:50%; flex-shrink:0; }
      .mig-pillar-right { display:flex; align-items:center; gap:14px; }
      .mig-pillar-val  { font-weight:700; min-width:40px; text-align:right; }
      .mig-pillar-pct  { color:#9ca3af; font-size:12px; min-width:30px; text-align:right; }
      .mig-chart-wrap  { height:200px; position:relative; }
      /* Hide React migration content when our custom panel is mounted */
      .dm-content:has(.mig-mount) > *:not(.mig-mount) { display: none !important; }
      .mig-tbl { width:100%; border-collapse:collapse; font-size:13px; }
      .mig-tbl th {
        padding:8px 12px; text-align:left; font-size:11px; font-weight:600;
        color:#9ca3af; text-transform:uppercase; letter-spacing:.04em;
        border-bottom:1px solid #e5e7eb;
      }
      .mig-tbl th.r { text-align:right; }
      .mig-tbl td { padding:10px 12px; border-bottom:1px solid #f3f4f6; color:#374151; }
      .mig-tbl td.r { text-align:right; font-weight:600; }
      .mig-tbl td.lin { color:#1a73e8; }
      .mig-tbl td.lift { color:#059669; font-weight:600; }
      .mig-run-btn {
        display:inline-flex; align-items:center; gap:6px;
        padding:6px 14px; border-radius:7px;
        border:1px solid #e5e7eb; background:#fff;
        font:inherit; font-size:12.5px; color:#374151; cursor:pointer;
      }
      .mig-run-btn:hover { background:#f3f4f6; }
      .mig-nav-active { background:var(--accent,#f1f5f9) !important; font-weight:600 !important; }
      @media(max-width:800px) {
        .mig-overview-topbar { flex-wrap:wrap; }
        .mig-kpis    { grid-template-columns:1fr 1fr; }
        .mig-discarded-kpis { grid-template-columns:1fr 1fr; }
        .mig-risk-grid { grid-template-columns:1fr 1fr; gap:20px; }
        .mig-two-col { grid-template-columns:1fr; }
        .mig-donut-wrap { flex-direction:column; align-items:flex-start; }
        .mig-donut-ring { width:220px; height:220px; flex-basis:220px; }
      }
    `;
    document.head.appendChild(st);
  }

  // ── State ──────────────────────────────────────────────────────────────
  let migData = null;
  let migView = "overview";  // overview | before_after | discarded

  // ── Fetch real data ────────────────────────────────────────────────────
  async function fetchMigData() {
    if (migData) return migData;
    try {
      const r = await fetch("/api/migration", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      migData = await r.json();
    } catch (e) {
      migData = null;
    }
    return migData;
  }

  // ── Build the donut SVG ────────────────────────────────────────────────
  function buildDonut(val, pillars) {
    // pillars: [{label, value, color}]
    const size = 260, cx = 130, cy = 130, r = 110, stroke = 36;
    const circ = 2 * Math.PI * r;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.cssText = "width:100%;height:100%;transform:rotate(-90deg)";

    const total = pillars.reduce((s, p) => s + p.value, 0) || 1;
    const base = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    base.setAttribute("cx", cx); base.setAttribute("cy", cy); base.setAttribute("r", r);
    base.setAttribute("fill", "none"); base.setAttribute("stroke", "#111827");
    base.setAttribute("stroke-width", stroke + 4);
    svg.appendChild(base);
    let offset = 0;
    pillars.forEach(p => {
      const frac = p.value / total;
      const dash = frac * circ;
      const seg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      seg.setAttribute("cx", cx); seg.setAttribute("cy", cy); seg.setAttribute("r", r);
      seg.setAttribute("fill", "none"); seg.setAttribute("stroke", p.color);
      seg.setAttribute("stroke-width", stroke);
      seg.setAttribute("stroke-dasharray", `${dash} ${circ - dash}`);
      seg.setAttribute("stroke-dashoffset", `-${offset}`);
      svg.appendChild(seg);
      offset += dash;
    });
    return svg;
  }

  // ── Render page ────────────────────────────────────────────────────────
  async function renderMigPage(container) {
    container.replaceChildren();
    const d = await fetchMigData();
    if (!d || !d.ready) {
      const msg = document.createElement("p");
      msg.style.cssText = "color:#9ca3af;font-size:13px;padding:20px";
      msg.textContent = (d && d.hint) || "Migration data not available. Run 03_Trust_Score.py first.";
      container.appendChild(msg);
      return;
    }

    const page = document.createElement("div");
    page.className = "mig-page";

    // ── Top bar: Azure→GCP + pipeline steps ────────────────────────────
    const topBar = document.createElement("div");
    topBar.className = "mig-top-bar mig-overview-topbar";

    // Cloud route
    const route = document.createElement("div");
    route.className = "mig-cloud-route";
    const azBadge = document.createElement("span");
    azBadge.className = "mig-cloud-badge azure"; azBadge.textContent = "Azure";
    const arrow = document.createElement("i");
    arrow.className = "ti ti-arrow-right mig-arrow-icon";
    const gcpBadge = document.createElement("span");
    gcpBadge.className = "mig-cloud-badge gcp"; gcpBadge.textContent = "GCP";
    route.append(azBadge, arrow, gcpBadge);

    // Pipeline steps
    const steps = document.createElement("div");
    steps.className = "mig-steps";
    (d.pipeline_steps || []).forEach(step => {
      const btn = document.createElement("div");
      btn.className = "mig-step-btn";
      btn.innerHTML = `<i class="ti ti-circle-check"></i> ${step.label}`;
      steps.appendChild(btn);
    });

    // Run button
    const runBtn = document.createElement("button");
    runBtn.className = "mig-run-btn";
    runBtn.innerHTML = '<i class="ti ti-player-play" style="font-size:12px;color:#009688"></i> Run Migration pipeline';
    runBtn.title = "Demo mode — no actual migration runs";
    const runRow = document.createElement("div");
    runRow.className = "mig-run-row";
    runRow.appendChild(runBtn);
    page.appendChild(runRow);
    topBar.append(route, steps);
    page.appendChild(topBar);

    // ── 4 KPI cards ────────────────────────────────────────────────────
    const kpis = document.createElement("div");
    kpis.className = "mig-kpis";
    [
      { icon:"ti-arrow-down", label:"Trust before",    val:d.trust_before, sub:"Pre-migration",         color:"#169968" },
      { icon:"ti-arrow-up", label:"Trust after",     val:d.trust_after,  sub:`+${d.trust_delta} pts`, color:"#169968" },
      { icon:"ti-database", label:"Records migrated",val:Number(d.records_migrated).toLocaleString(), sub:`${d.retained_pct}% retained`, color:"#111827" },
      { icon:"ti-trash",    label:"Records discarded",val:Number(d.records_discarded).toLocaleString(), sub:"Anomalies removed", color:"#dc2626" },
    ].forEach(k => {
      const card = document.createElement("div");
      card.className = "mig-kpi";
      const iconRow = document.createElement("div");
      iconRow.className = "mig-kpi-icon-row";
      iconRow.innerHTML = `<i class="ti ${k.icon}"></i> ${k.label}`;
      const valEl = document.createElement("div");
      valEl.className = "mig-kpi-val";
      valEl.style.color = k.color;
      valEl.textContent = k.val;
      const sub = document.createElement("div");
      sub.className = "mig-kpi-sub";
      sub.textContent = k.sub;
      card.append(iconRow, valEl, sub);
      kpis.appendChild(card);
    });
    page.appendChild(kpis);

    // ── Pillar contributions donut ──────────────────────────────────────
    const p = d.pillar_after || {};
    const pillars3 = [
      { label:"Anomaly health",  value:Number(p.anomaly ?? 0), color:"#0d9488" },
      { label:"Drift health",    value:Number(p.drift ?? 0), color:"#2563eb" },
      { label:"Business health", value:Number(p.business ?? 0), color:"#ea580c" },
    ];
    const totalPillar = pillars3.reduce((s, x) => s + x.value, 0);

    const donutCard = document.createElement("div");
    donutCard.className = "mig-card mig-overview-card";
    const donutTitle = document.createElement("div");
    donutTitle.className = "mig-card-title";
    donutTitle.innerHTML = '<i class="ti ti-chart-pie"></i> Pillar contributions to trust after migration';
    donutCard.appendChild(donutTitle);

    const donutWrap = document.createElement("div");
    donutWrap.className = "mig-donut-wrap";

    // Donut ring
    const ringWrap = document.createElement("div");
    ringWrap.className = "mig-donut-ring";
    ringWrap.appendChild(buildDonut(d.trust_after, pillars3));
    const center = document.createElement("div");
    center.className = "mig-donut-center";
    const cv = document.createElement("div"); cv.className = "mig-donut-val"; cv.textContent = d.trust_after;
    const cl = document.createElement("div"); cl.className = "mig-donut-lbl"; cl.textContent = "Trust after";
    center.append(cv, cl);
    ringWrap.appendChild(center);

    // Pillar list
    const pillarList = document.createElement("div");
    pillarList.className = "mig-pillar-list";
    pillars3.forEach(pl => {
      const row = document.createElement("div");
      row.className = "mig-pillar-row";
      const left = document.createElement("div");
      left.className = "mig-pillar-left";
      const dot = document.createElement("span");
      dot.className = "mig-pillar-dot";
      dot.style.background = pl.color;
      left.append(dot, document.createTextNode(pl.label));
      const right = document.createElement("div");
      right.className = "mig-pillar-right";
      const valEl = document.createElement("span");
      valEl.className = "mig-pillar-val";
      valEl.style.color = pl.color;
      valEl.textContent = pl.value.toFixed(1);
      const pctEl = document.createElement("span");
      pctEl.className = "mig-pillar-pct";
      pctEl.textContent = Math.round(pl.value / totalPillar * 100) + "%";
      right.append(valEl, pctEl);
      row.append(left, right);
      pillarList.appendChild(row);
    });

    donutWrap.append(ringWrap, pillarList);
    donutCard.appendChild(donutWrap);
    page.appendChild(donutCard);

    // ── Bottom two-col: bar chart + per-lineage table ───────────────────
    const twoCol = document.createElement("div");
    twoCol.className = "mig-two-col";

    // Bar chart card
    const barCard = document.createElement("div");
    barCard.className = "mig-card mig-overview-card";
    barCard.style.marginBottom = "0";
    const barTitle = document.createElement("div");
    barTitle.className = "mig-card-title";
    barTitle.innerHTML = '<i class="ti ti-chart-bar"></i> Trust pillars — before vs after migration';
    barCard.appendChild(barTitle);
    const chartWrap = document.createElement("div");
    chartWrap.className = "mig-chart-wrap";
    const canvas = document.createElement("canvas");
    chartWrap.appendChild(canvas);
    barCard.appendChild(chartWrap);

    // Draw bar chart using Chart.js global if available, else SVG fallback
    setTimeout(() => {
      if (window.Chart) {
        const before = d.pillar_before || {};
        const after  = d.pillar_after  || {};
        new window.Chart(canvas, {
          type: "bar",
          data: {
            labels: ["Anomaly health", "Drift health", "Business health"],
            datasets: [
              { label:"Before", data:[before.anomaly||0, before.drift||0, before.business||0], backgroundColor:"#f97316", borderRadius:3 },
              { label:"After",  data:[after.anomaly||0,  after.drift||0,  after.business||0],  backgroundColor:"#14a38b", borderRadius:3 },
            ],
          },
          options: {
            responsive:true, maintainAspectRatio:false,
            plugins:{
              legend:{
                position:"bottom",
                labels:{
                  boxWidth:12,
                  font:{ size:11 },
                  filter: (item) => item.text === "Before" || item.text === "After",
                }
              }
            },
            scales:{
              x:{ grid:{ display:false }, ticks:{ font:{ size:10 } } },
              y:{ min:0, max:100, ticks:{ font:{ size:10 }, callback: v => v } },
            },
          },
        });
      } else {
        // SVG fallback bar chart
        const svgNS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(svgNS, "svg");
        svg.setAttribute("viewBox","0 0 480 180");
        svg.style.cssText = "width:100%;height:100%";
        const before = d.pillar_before || {};
        const after  = d.pillar_after  || {};
        const groups = [
          { label:"Anomaly", b:before.anomaly||0, a:after.anomaly||0 },
          { label:"Drift",   b:before.drift||0,   a:after.drift||0   },
          { label:"Business",b:before.business||0,a:after.business||0},
        ];
        const maxH = 130, bw = 30, gap = 10, startX = 50;
        // Grid lines
        [0,25,50,75,100].forEach(v => {
          const y = 10 + maxH * (1 - v/100);
          const line = document.createElementNS(svgNS,"line");
          line.setAttribute("x1",startX); line.setAttribute("x2",470);
          line.setAttribute("y1",y); line.setAttribute("y2",y);
          line.setAttribute("stroke","#f1f5f9"); line.setAttribute("stroke-width","1");
          svg.appendChild(line);
          const lbl = document.createElementNS(svgNS,"text");
          lbl.setAttribute("x",startX-5); lbl.setAttribute("y",y+4);
          lbl.setAttribute("text-anchor","end"); lbl.setAttribute("font-size","9"); lbl.setAttribute("fill","#9ca3af");
          lbl.textContent = v;
          svg.appendChild(lbl);
        });
        groups.forEach((g, i) => {
          const gx = startX + i * (bw*2 + gap + 40);
          [[g.b,"#f97316"],[g.a,"#14a38b"]].forEach(([v,col],j) => {
            const bh = maxH * v / 100;
            const rect = document.createElementNS(svgNS,"rect");
            rect.setAttribute("x", gx + j*(bw+4));
            rect.setAttribute("y", 10 + maxH - bh);
            rect.setAttribute("width", bw); rect.setAttribute("height", bh);
            rect.setAttribute("fill", col); rect.setAttribute("rx","2");
            svg.appendChild(rect);
          });
          const lbl = document.createElementNS(svgNS,"text");
          lbl.setAttribute("x", gx + bw); lbl.setAttribute("y","168");
          lbl.setAttribute("text-anchor","middle"); lbl.setAttribute("font-size","9"); lbl.setAttribute("fill","#6b7280");
          lbl.textContent = g.label;
          svg.appendChild(lbl);
        });
        // Legend
        [["Before","#f97316",320],["After","#14a38b",380]].forEach(([lbl,col,x]) => {
          const rect = document.createElementNS(svgNS,"rect");
          rect.setAttribute("x",x); rect.setAttribute("y","155");
          rect.setAttribute("width","10"); rect.setAttribute("height","10");
          rect.setAttribute("fill",col);
          const t = document.createElementNS(svgNS,"text");
          t.setAttribute("x",x+14); t.setAttribute("y","164");
          t.setAttribute("font-size","9"); t.setAttribute("fill","#6b7280");
          t.textContent = lbl;
          svg.append(rect, t);
        });
        canvas.parentNode.replaceChild(svg, canvas);
      }
    }, 200);

    // Per-lineage trust lift table
    const liftCard = document.createElement("div");
    liftCard.className = "mig-card mig-overview-card";
    liftCard.style.marginBottom = "0";
    const liftTitle = document.createElement("div");
    liftTitle.className = "mig-card-title";
    liftTitle.innerHTML = '<i class="ti ti-chart-line"></i> Per-lineage trust lift';
    liftCard.appendChild(liftTitle);

    const tbl = document.createElement("table");
    tbl.className = "mig-tbl";
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    ["Lineage","Before","After","Lift"].forEach((h, i) => {
      const th = document.createElement("th");
      th.className = i > 0 ? "r" : "";
      th.textContent = h;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    tbl.appendChild(thead);
    const tbody = document.createElement("tbody");
    (d.lineage_lift || []).forEach(row => {
      const tr = document.createElement("tr");
      [
        { text:row.lineage, cls:"lin" },
        { text:row.before.toFixed(1), cls:"r" },
        { text:row.after.toFixed(1),  cls:"r" },
        { text:"+" + row.lift.toFixed(1), cls:"lift r" },
      ].forEach(cell => {
        const td = document.createElement("td");
        td.className = cell.cls;
        td.textContent = cell.text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    liftCard.appendChild(tbl);

    twoCol.append(barCard, liftCard);
    page.appendChild(twoCol);
    container.appendChild(page);
  }

  // ── Patch the migration content area ──────────────────────────────────
  // ── Patch the migration content area ──────────────────────────────────
  function patchMigrationSidebar() {
    if (!window.location.hash.includes("/migration")) return;
    const sidebar = document.querySelector(".dm-sidebar");
    if (!sidebar) return;

    // Rename existing sidebar items to match the reference design
    sidebar.querySelectorAll(".dm-side-tab").forEach(btn => {
      const span = btn.querySelector("span");
      if (!span) return;
      const text = span.textContent.trim();
      // "Oracle → BigQuery" (Oracle comes first) → rename to Overview
      const oracleFirst = text.indexOf("Oracle") < text.indexOf("BigQuery") && text.includes("Oracle") && text.includes("BigQuery");
      // "BigQuery → Oracle" (BigQuery comes first) → rename to Before vs After
      const bqFirst = text.indexOf("BigQuery") < text.indexOf("Oracle") && text.includes("Oracle") && text.includes("BigQuery");

      if (oracleFirst && btn.dataset.migPatched !== "overview") {
        if (btn.dataset.migOriginalText === undefined) btn.dataset.migOriginalText = span.textContent;
        const icon = btn.querySelector("i");
        if (icon && btn.dataset.migOriginalIcon === undefined) btn.dataset.migOriginalIcon = icon.className;
        span.textContent = "Overview";
        if (icon) { icon.className = "ti ti-layout-dashboard"; icon.style.fontSize = "17px"; icon.style.flexShrink = "0"; }
        btn.dataset.migPatched = "overview";
      }
      if (bqFirst && btn.dataset.migPatched !== "beforeafter") {
        if (btn.dataset.migOriginalText === undefined) btn.dataset.migOriginalText = span.textContent;
        const icon = btn.querySelector("i");
        if (icon && btn.dataset.migOriginalIcon === undefined) btn.dataset.migOriginalIcon = icon.className;
        span.textContent = "Before vs After";
        if (icon) { icon.className = "ti ti-arrows-transfer-up"; icon.style.fontSize = "17px"; icon.style.flexShrink = "0"; }
        btn.dataset.migPatched = "beforeafter";
      }
    });

    // Add "Discarded Records" if not already there
    if (!sidebar.querySelector("[data-mig-discarded]")) {
      const existing = [...sidebar.querySelectorAll(".dm-side-tab")];
      const lastMigBtn = existing.filter(b => b.dataset.migPatched).pop();
      if (lastMigBtn) {
        const discBtn = document.createElement("button");
        discBtn.className = "dm-side-tab";
        discBtn.setAttribute("data-mig-discarded", "true");
        discBtn.innerHTML = '<i class="ti ti-trash" style="font-size:17px;flex-shrink:0"></i> <span>Discarded Records</span>';
        discBtn.addEventListener("click", () => {
          // Show discarded records view
          const appShell = document.querySelector(".dm-wrap");
          const appMain = appShell?.querySelector(".dm-content") || appShell?.querySelector("main");
          if (!appMain) return;
          const mount = appMain.querySelector(".mig-mount");
          if (mount) {
            renderDiscardedView(mount);
          }
          // Active state
          sidebar.querySelectorAll(".dm-side-tab").forEach(b => b.classList.remove("active"));
          discBtn.classList.add("active");
        });
        lastMigBtn.after(discBtn);
      }
    }

    // Add Overview click handler to restore main migration view
    sidebar.querySelectorAll(".dm-side-tab").forEach(btn => {
      if (btn.dataset.migPatched === "overview" && !btn.dataset.migOverviewWired) {
        btn.dataset.migOverviewWired = "true";
        btn.addEventListener("click", () => {
          if (!window.location.hash.includes("/migration")) return;
          const appShell = document.querySelector(".dm-wrap");
          const appMain = appShell?.querySelector(".dm-content") || appShell?.querySelector("main");
          if (!appMain) return;
          const mount = appMain.querySelector(".mig-mount");
          if (mount) renderMigPage(mount);
          sidebar.querySelectorAll(".dm-side-tab").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        });
      }
      if (btn.dataset.migPatched === "beforeafter" && !btn.dataset.migBaWired) {
        btn.dataset.migBaWired = "true";
        btn.addEventListener("click", () => {
          if (!window.location.hash.includes("/migration")) return;
          const appShell = document.querySelector(".dm-wrap");
          const appMain = appShell?.querySelector(".dm-content") || appShell?.querySelector("main");
          if (!appMain) return;
          const mount = appMain.querySelector(".mig-mount");
          if (mount) renderBeforeAfterView(mount);
          sidebar.querySelectorAll(".dm-side-tab").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        });
      }
    });
  }

  async function renderDiscardedView(container) {
    container.replaceChildren();
    const page = document.createElement("div");
    page.className = "mig-page";
    const head = document.createElement("div");
    head.className = "mig-discarded-head";
    const hdr = document.createElement("div");
    hdr.className = "dm-page-head";
    hdr.style.marginBottom = "0";
    hdr.innerHTML = '<div class="dm-page-title">Discarded records</div><div class="dm-page-sub">Records removed by the cleaning step before migration — carried anomalies that would have degraded post-migration trust.</div>';
    const countBadge = document.createElement("span");
    countBadge.className = "mig-discarded-count";
    head.append(hdr, countBadge);
    page.appendChild(head);

    try {
      const r = await fetch("/api/migration/discarded", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      const records = Array.isArray(j.records) ? j.records : [];
      const total = Number(j.total ?? records.length);
      countBadge.textContent = total.toLocaleString();
      if (!j.ready) throw new Error(j.hint || "Discarded records are not available.");

      const average = key => records.length ? records.reduce((sum, rec) => sum + Number(rec[key] || 0), 0) / records.length : 0;
      const pct = value => `${value.toFixed(1)}%`;
      const kpis = [
        ["ti-chart-bar", "Avg trust score", average("trust_score"), "#c40018"],
        ["ti-alert-triangle", "Avg anomaly health", average("anomaly_health"), "#fb8c00"],
        ["ti-wave-sine", "Avg drift health", average("drift_health"), "#009688"],
        ["ti-briefcase", "Avg business health", average("business_health"), "#169968"],
      ];
      const kpiGrid = document.createElement("div");
      kpiGrid.className = "mig-discarded-kpis";
      kpis.forEach(([icon, label, value, color]) => {
        const card = document.createElement("div");
        card.className = "mig-discarded-kpi";
        const title = document.createElement("div");
        title.className = "mig-discarded-kpi-label";
        const glyph = document.createElement("i");
        glyph.className = `ti ${icon}`;
        glyph.style.color = color;
        const text = document.createElement("span");
        text.textContent = label;
        title.append(glyph, text);
        const valueEl = document.createElement("div");
        valueEl.className = "mig-discarded-kpi-value";
        valueEl.style.color = color;
        valueEl.textContent = `${label === "Avg trust score" || label === "Avg anomaly health" ? Math.round(value) : value.toFixed(1)}%`;
        card.append(title, valueEl);
        kpiGrid.appendChild(card);
      });
      page.appendChild(kpiGrid);

      const riskOrder = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
      const riskColors = {
        CRITICAL: ["#b91c1c", "#fff1f2", "#fecaca"],
        HIGH: ["#b45309", "#fff7e8", "#fed7aa"],
        MEDIUM: ["#075985", "#eff8fb", "#bae6fd"],
        LOW: ["#64748b", "#f5f5f5", "#e5e7eb"],
      };
      const riskCounts = Object.fromEntries(riskOrder.map(level => [level, 0]));
      records.forEach(rec => { if (riskCounts[rec.risk_level] !== undefined) riskCounts[rec.risk_level]++; });
      const riskCard = document.createElement("div");
      riskCard.className = "mig-card mig-discarded-section";
      const riskTitle = document.createElement("div");
      riskTitle.className = "mig-card-title";
      riskTitle.innerHTML = '<i class="ti ti-shield-exclamation" style="color:#dc2626"></i> Risk distribution <i class="ti ti-info-circle" title="Risk groups are based on each record’s trust score." style="font-size:13px;color:#64748b"></i>';
      riskCard.appendChild(riskTitle);
      const riskGrid = document.createElement("div");
      riskGrid.className = "mig-risk-grid";
      riskOrder.forEach(level => {
        const count = riskCounts[level];
        const share = records.length ? count / records.length * 100 : 0;
        const [color, background, border] = riskColors[level];
        const item = document.createElement("div");
        const label = document.createElement("span");
        label.className = "mig-risk-label";
        label.style.cssText = `color:${color};background:${background};border-color:${border}`;
        label.textContent = level;
        const amount = document.createElement("div");
        amount.className = "mig-risk-count";
        amount.textContent = count.toLocaleString();
        const portion = document.createElement("div");
        portion.className = "mig-risk-share";
        portion.textContent = `${share.toFixed(1)}% of discarded`;
        const track = document.createElement("div");
        track.className = "mig-risk-track";
        const fill = document.createElement("div");
        fill.className = "mig-risk-fill";
        fill.style.cssText = `width:${share}%;background:${color}`;
        track.appendChild(fill);
        item.append(label, amount, portion, track);
        riskGrid.appendChild(item);
      });
      riskCard.appendChild(riskGrid);
      page.appendChild(riskCard);

      const detailCard = document.createElement("div");
      detailCard.className = "mig-card mig-discarded-section";
      const detailTitle = document.createElement("div");
      detailTitle.className = "mig-card-title";
      detailTitle.innerHTML = '<i class="ti ti-table" style="color:#dc2626"></i> Record details <i class="ti ti-info-circle" title="Discarded records and their trust health scores." style="font-size:13px;color:#64748b"></i>';
      detailCard.appendChild(detailTitle);
      const lineageKey = rec => String(rec.lineage || "Unknown").trim().toUpperCase();
      const knownLineages = ["HISTORICAL", "PREVIOUS_MONTH", "CURRENT_MONTH"];
      const lineages = [...new Set(records.map(lineageKey))].sort((a, b) => {
        const ai = knownLineages.indexOf(a), bi = knownLineages.indexOf(b);
        if (ai < 0 && bi < 0) return a.localeCompare(b);
        if (ai < 0) return 1;
        if (bi < 0) return -1;
        return ai - bi;
      });
      const lineageCounts = Object.fromEntries(lineages.map(lineage => [lineage, records.filter(rec => lineageKey(rec) === lineage).length]));
      const filters = document.createElement("div");
      filters.className = "mig-discarded-filters";
      const filterLabel = document.createElement("span");
      filterLabel.className = "mig-discarded-filter-label";
      filterLabel.textContent = "LINEAGE:";
      filters.appendChild(filterLabel);
      let selectedLineage = "ALL";
      const filterButtons = new Map();
      const addFilter = (key, label, count) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mig-discarded-filter";
        button.textContent = `${label} (${count})`;
        button.addEventListener("click", () => {
          selectedLineage = key;
          currentPage = 0;
          filterButtons.forEach((el, value) => el.classList.toggle("active", value === key));
          drawRows();
        });
        filterButtons.set(key, button);
        filters.appendChild(button);
      };
      addFilter("ALL", "All", records.length);
      lineages.forEach(lineage => addFilter(lineage, lineage, lineageCounts[lineage]));
      filterButtons.get("ALL")?.classList.add("active");
      detailCard.appendChild(filters);

      const tableWrap = document.createElement("div");
      tableWrap.style.overflowX = "auto";
      const table = document.createElement("table");
      table.className = "mig-tbl mig-discarded-table";
      const thead = document.createElement("thead");
      const headerRow = document.createElement("tr");
      const headings = ["#", "Record", "Business ID", "Lineage", "Trust", "Anomaly", "Drift", "Business", "Risk"];
      headings.forEach((heading, index) => {
        const th = document.createElement("th");
        if ([4, 5, 6, 7, 8].includes(index)) th.style.textAlign = "right";
        th.textContent = heading;
        if (index === 4) {
          th.style.color = "#2563eb";
          const arrow = document.createElement("span");
          arrow.textContent = " ↑";
          th.appendChild(arrow);
        } else if ([5, 6, 7].includes(index)) {
          const arrow = document.createElement("span");
          arrow.textContent = " ↕";
          arrow.style.color = "#9ca3af";
          th.appendChild(arrow);
        }
        headerRow.appendChild(th);
      });
      thead.appendChild(headerRow);
      const tbody = document.createElement("tbody");
      table.append(thead, tbody);
      tableWrap.appendChild(table);
      detailCard.appendChild(tableWrap);
      const footer = document.createElement("div");
      footer.className = "mig-discarded-footer";
      detailCard.appendChild(footer);

      const rowLimit = 50;
      let currentPage = 0;
      const drawRows = () => {
        const visible = records.filter(rec => selectedLineage === "ALL" || lineageKey(rec) === selectedLineage);
        const pageCount = Math.max(1, Math.ceil(visible.length / rowLimit));
        currentPage = Math.min(currentPage, pageCount - 1);
        const start = currentPage * rowLimit;
        tbody.replaceChildren();
        visible.slice(start, start + rowLimit).forEach((rec, offset) => {
          const tr = document.createElement("tr");
          const cells = [
            [start + offset + 1, "muted"],
            [`#${rec.record_id}`, ""],
            [rec.customer_id || "—", ""],
            [rec.lineage || "Unknown", "lineage"],
            [pct(Number(rec.trust_score || 0)), "trust"],
            [pct(Number(rec.anomaly_health || 0)), "metric"],
            [pct(Number(rec.drift_health || 0)), "metric"],
            [pct(Number(rec.business_health || 0)), "metric"],
            [rec.risk_level || "MEDIUM", "risk"],
          ];
          cells.forEach(([value, kind], index) => {
            const td = document.createElement("td");
            if (index >= 4) td.style.textAlign = "right";
            if (kind === "muted") td.className = "muted";
            if (kind === "lineage") {
              const tag = document.createElement("span");
              tag.className = "mig-discarded-lineage";
              tag.textContent = String(value).toUpperCase();
              td.appendChild(tag);
            } else if (kind === "risk") {
              const level = String(value);
              const [color, background, border] = riskColors[level] || riskColors.MEDIUM;
              const tag = document.createElement("span");
              tag.className = "mig-discarded-risk-pill";
              tag.style.cssText = `color:${color};background:${background};border-color:${border}`;
              tag.textContent = level;
              td.appendChild(tag);
            } else {
              td.textContent = String(value);
              if (kind === "trust" || kind === "metric") {
                const score = Number(String(value).replace("%", ""));
                td.style.color = score < 60 ? "#dc2626" : score < 75 ? "#f57c00" : "#009688";
                td.style.fontWeight = "600";
              }
            }
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
        const showingEnd = Math.min(start + rowLimit, visible.length);
        footer.replaceChildren();
        const summary = document.createElement("span");
        summary.textContent = visible.length ? `Showing ${start + 1}–${showingEnd} of ${visible.length} records` : "No discarded records for this lineage";
        const controls = document.createElement("div");
        controls.className = "mig-discarded-pages";
        const first = document.createElement("button");
        first.textContent = "«";
        first.disabled = currentPage === 0;
        first.addEventListener("click", () => { currentPage = 0; drawRows(); });
        const prev = document.createElement("button");
        prev.textContent = "‹ Prev";
        prev.disabled = currentPage === 0;
        prev.addEventListener("click", () => { currentPage--; drawRows(); });
        const pageLabel = document.createElement("span");
        pageLabel.textContent = `${currentPage + 1} / ${pageCount}`;
        const next = document.createElement("button");
        next.textContent = "Next ›";
        next.disabled = currentPage >= pageCount - 1;
        next.addEventListener("click", () => { currentPage++; drawRows(); });
        const last = document.createElement("button");
        last.textContent = "»";
        last.disabled = currentPage >= pageCount - 1;
        last.addEventListener("click", () => { currentPage = pageCount - 1; drawRows(); });
        controls.append(first, prev, pageLabel, next, last);
        footer.append(summary, controls);
      };
      page.appendChild(detailCard);
      drawRows();
    } catch (e) {
      const error = document.createElement("p");
      error.style.color = "#9ca3af";
      error.textContent = "Could not load discarded records.";
      page.appendChild(error);
    }
    container.appendChild(page);
  }

  async function renderBeforeAfterView(container) {
    container.replaceChildren();
    const d = await fetchMigData();
    const page = document.createElement("div");
    page.className = "mig-page";

    // Header
    const hdr = document.createElement("div");
    hdr.className = "dm-page-head";
    hdr.style.marginBottom = "18px";
    hdr.innerHTML = '<div class="dm-page-title">Before vs after</div><div class="dm-page-sub">How the Azure \u2192 GCP migration changed data trust, factor by factor and lineage by lineage.</div>';
    page.appendChild(hdr);

    if (!d || !d.ready) {
      page.appendChild(Object.assign(document.createElement("p"), { textContent: "No migration data available.", style: "color:#9ca3af;font-size:13px" }));
      container.appendChild(page);
      return;
    }

    // ── Trust pillars bar chart ──────────────────────────────────────────
    const barCard = document.createElement("div");
    barCard.className = "mig-card mig-before-after-chart-card";
    barCard.style.marginBottom = "16px";
    const barTitle = document.createElement("div");
    barTitle.className = "mig-card-title";
    barTitle.innerHTML = '<i class="ti ti-arrows-sort"></i> Trust pillars \u2014 before vs after';
    barCard.appendChild(barTitle);
    const chartWrap = document.createElement("div");
    chartWrap.style.cssText = "height:260px;position:relative";
    const canvas = document.createElement("canvas");
    chartWrap.appendChild(canvas);
    barCard.appendChild(chartWrap);
    page.appendChild(barCard);

    const before = d.pillar_before || {};
    const after  = d.pillar_after  || {};

    setTimeout(() => {
      if (Chart) {
        // Destroy any existing Chart.js instance on this canvas
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();

        new Chart(canvas, {
          type: "bar",
          data: {
            labels: ["Anomaly health", "Drift health", "Business health"],
            datasets: [
              { label:"Before", data:[before.anomaly||0, before.drift||0, before.business||0], backgroundColor:"#ff9800", borderRadius:5, barPercentage:0.75, categoryPercentage:0.5 },
              { label:"After",  data:[after.anomaly||0,  after.drift||0,  after.business||0],  backgroundColor:"#169968", borderRadius:5, barPercentage:0.75, categoryPercentage:0.5 },
            ],
          },
          options: {
            responsive:true, maintainAspectRatio:false,
            layout: { padding: { top: 10, bottom: 0 } },
            plugins:{
              tooltip:{
                enabled:true,
                mode:"nearest",
                intersect:true,
                backgroundColor:"#111827",
                titleColor:"#ffffff",
                bodyColor:"#ffffff",
                padding:9,
                displayColors:true,
                callbacks:{
                  label: context => `${context.dataset.label}: ${Number(context.parsed.y).toFixed(1)}`
                }
              },
              legend:{
                position:"bottom",
                align:"center",
                labels:{ boxWidth:14, font:{ size:12 }, padding:24,
                  filter: (item) => item.text === "Before" || item.text === "After" }
              }
            },
            scales:{
              x:{
                grid:{ display:false },
                ticks:{ font:{ size:11 }, color:"#9ca3af" },
                border:{ display:false }
              },
              y:{
                min:0, max:100,
                grid:{ color:"rgba(200,200,200,0.3)", drawBorder:false },
                ticks:{ font:{ size:11 }, color:"#9ca3af", stepSize:20, callback: v => v },
                border:{ display:false }
              },
            },
          },
        });
      } else {
        chartWrap.textContent = "Chart could not be loaded.";
      }
    }, 150);

    // ── Per-lineage trust lift table ────────────────────────────────────
    const liftCard = document.createElement("div");
    liftCard.className = "mig-card";
    const liftTitle = document.createElement("div");
    liftTitle.className = "mig-card-title";
    liftTitle.innerHTML = '<i class="ti ti-chart-line"></i> Per-lineage trust lift';
    liftCard.appendChild(liftTitle);

    const tbl = document.createElement("table");
    tbl.className = "mig-tbl";
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    ["Lineage","Before","After","Lift"].forEach((h,i) => {
      const th = document.createElement("th");
      th.className = i > 0 ? "r" : "";
      th.textContent = h;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    tbl.appendChild(thead);
    const tbody = document.createElement("tbody");
    (d.lineage_lift || []).forEach(row => {
      const tr = document.createElement("tr");
      [
        { text:row.lineage, cls:"lin" },
        { text:row.before.toFixed(1), cls:"r" },
        { text:row.after.toFixed(1),  cls:"r" },
        { text:"+" + row.lift.toFixed(1), cls:"lift r" },
      ].forEach(cell => {
        const td = document.createElement("td");
        td.className = cell.cls;
        td.textContent = cell.text;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    liftCard.appendChild(tbl);
    page.appendChild(liftCard);

    container.appendChild(page);
  }

  function patchMigrationPage() {
    const hash = window.location.hash;
    const appShell = document.querySelector(".dm-wrap");
    const appMain  = appShell?.querySelector(".dm-content") || appShell?.querySelector("main");
    if (!appMain) return;

    if (!hash.includes("/migration")) {
      const sidebar = document.querySelector(".dm-sidebar");
      if (sidebar) {
        sidebar.querySelectorAll("[data-mig-discarded]").forEach(node => node.remove());
        sidebar.querySelectorAll(".dm-side-tab[data-mig-patched]").forEach(btn => {
          const span = btn.querySelector("span");
          const icon = btn.querySelector("i");
          if (span && btn.dataset.migOriginalText !== undefined) span.textContent = btn.dataset.migOriginalText;
          if (icon && btn.dataset.migOriginalIcon !== undefined) icon.className = btn.dataset.migOriginalIcon;
          if (icon) { icon.style.fontSize = ""; icon.style.flexShrink = ""; }
          delete btn.dataset.migPatched;
          delete btn.dataset.migOverviewWired;
          delete btn.dataset.migBaWired;
          delete btn.dataset.migOriginalText;
          delete btn.dataset.migOriginalIcon;
        });
      }
      appMain.querySelectorAll(":scope > *:not(.mig-mount)").forEach(node => {
        if (node.dataset.migOriginalDisplay !== undefined) {
          node.style.display = node.dataset.migOriginalDisplay;
          delete node.dataset.migOriginalDisplay;
        } else if (node.style.display === "none") {
          node.style.display = "";
        }
      });
      const oldMount = appMain.querySelector(":scope > .mig-mount");
      if (oldMount) oldMount.style.display = "none";
      return;
    }

    // Always hide React-rendered migration content (Oracle→BigQuery error, pipeline output not found)
    appMain.querySelectorAll(":scope > *:not(.mig-mount)").forEach(n => {
      if (n.dataset.migOriginalDisplay === undefined) n.dataset.migOriginalDisplay = n.style.display;
      n.style.display = "none";
    });

    // Patch sidebar labels every time
    patchMigrationSidebar();

    // Create mount point if not present
    let mount = appMain.querySelector(".mig-mount");
    if (!mount) {
      mount = document.createElement("div");
      mount.className = "mig-mount";
      mount.style.cssText = "min-width:0;width:100%;padding:20px 22px 30px;box-sizing:border-box";
      appMain.appendChild(mount);

      // Show overview by default on first mount
      if (!mount.children.length) {
        renderMigPage(mount);
      }
    }
    mount.style.display = "";
  }

  // ── Wire navigation ────────────────────────────────────────────────────
  window.addEventListener("hashchange", () => {
    if (window.location.hash.includes("/migration")) {
      setTimeout(patchMigrationPage, 300);
    } else {
      patchMigrationPage();
    }
  });

  new MutationObserver(() => {
    if (window.location.hash.includes("/migration")) {
      patchMigrationPage();
      patchMigrationSidebar();
    } else {
      patchMigrationPage();
    }
  }).observe(document.body, { childList: true, subtree: false });

  setTimeout(patchMigrationPage, 500);

})();
