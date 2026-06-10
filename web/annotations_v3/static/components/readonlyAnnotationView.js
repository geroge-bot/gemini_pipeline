export function renderReadonlyAnnotation(container, context) {
  const results = context.values?.stage_results || {};
  const rows = Object.entries(results).map(([stage, record]) => `
    <section class="readonlyRecord">
      <strong>${stage}</strong>
      <pre>${JSON.stringify(record.values || {}, null, 2)}</pre>
    </section>
  `);
  container.innerHTML = rows.length ? rows.join("") : '<p class="mutedText">暂无上游记录</p>';
}
