// PCT CRM — Chart.js chart builders, shared across dashboard + reports.
// Colors match the app's pine-green + safety-amber design system, not
// Chart.js's or an earlier mint-green theme's defaults.

function renderRevenueChart(canvasId, labels, values) {
  const el = document.getElementById(canvasId);
  if (!el || typeof Chart === 'undefined') return;
  new Chart(el, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Revenue (₹)',
        data: values,
        backgroundColor: '#0b5c33',
        borderRadius: 6,
        maxBarThickness: 40,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { callback: (v) => '₹' + v.toLocaleString('en-IN') } },
      },
    },
  });
}

function renderLineChart(canvasId, labels, datasets) {
  const el = document.getElementById(canvasId);
  if (!el || typeof Chart === 'undefined') return;
  new Chart(el, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

function renderDoughnutChart(canvasId, labels, values, colors) {
  const el = document.getElementById(canvasId);
  if (!el || typeof Chart === 'undefined') return;
  new Chart(el, {
    type: 'doughnut',
    data: { labels: labels, datasets: [{ data: values, backgroundColor: colors }] },
    options: { responsive: true, plugins: { legend: { position: 'right' } } },
  });
}

function renderHorizontalBar(canvasId, labels, values, color) {
  const el = document.getElementById(canvasId);
  if (!el || typeof Chart === 'undefined') return;
  new Chart(el, {
    type: 'bar',
    data: { labels: labels, datasets: [{ data: values, backgroundColor: color || '#0b5c33', borderRadius: 5 }] },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true } },
    },
  });
}
