const views = document.querySelectorAll('.view-panel');
document.querySelectorAll('.view-button, .side-button[data-view]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.view-button, .side-button[data-view]').forEach(b => {
      const selected = b.dataset.view === button.dataset.view;
      b.classList.toggle('active', selected);
      if (b.classList.contains('view-button')) b.setAttribute('aria-selected', selected ? 'true' : 'false');
      if (b.classList.contains('side-button')) b.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    views.forEach(view => { view.hidden = view.id !== button.dataset.view; });
    if (window.monthChart) window.monthChart.resize();
    if (window.categoryChart) window.categoryChart.resize();
  });
});
if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#a1b0b4';
  Chart.defaults.borderColor = '#2d4048';
  const data = window.dashboardData;
  window.monthChart = new Chart(document.getElementById('monthChart'), {
    type: 'bar', data: { labels: data.months.map(m => m.period), datasets: [
      { label: 'Receita', data: data.months.map(m => m.revenue), backgroundColor: '#c9f080', borderRadius: 5 },
      { label: 'Resultado bruto', data: data.months.map(m => m.profit), backgroundColor: '#6bc8bb', borderRadius: 5 }
    ] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
  });
  window.categoryChart = new Chart(document.getElementById('categoryChart'), {
    type: 'bar', data: { labels: data.categories.map(c => c.name), datasets: [
      { label: 'Receita', data: data.categories.map(c => c.revenue), backgroundColor: '#c9f080', borderRadius: 5 }
    ] }, options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
  });
}
let beforePrintView;
window.addEventListener('beforeprint', () => {
  beforePrintView = document.querySelector('.view-button.active')?.dataset.view;
  views.forEach(view => { view.hidden = false; });
  window.monthChart?.resize();
  window.categoryChart?.resize();
});
window.addEventListener('afterprint', () => {
  views.forEach(view => { view.hidden = view.id !== beforePrintView; });
});
document.querySelectorAll('[data-download-chart]').forEach(button => {
  button.addEventListener('click', () => {
    const chart = window[button.dataset.downloadChart];
    if (!chart) { alert('O gráfico precisa da biblioteca de gráficos para ser exportado.'); return; }
    const link = document.createElement('a');
    link.download = `${button.dataset.downloadChart}.png`;
    link.href = chart.toBase64Image();
    link.click();
  });
});
