import {getVisualizationResults} from "./core/apiClient.js";
import {renderImagePair} from "./components/imagePairView.js";
import {renderReadonlyAnnotation} from "./components/readonlyAnnotationView.js";
import {readDatasetContext} from "./core/datasetContext.js";

const root = document.getElementById("visualize-app");
const context = readDatasetContext(root);
const stageSelect = document.getElementById("stageSelect");
const pageSizeSelect = document.getElementById("pageSizeSelect");
const prevPageBtn = document.getElementById("prevPageBtn");
const nextPageBtn = document.getElementById("nextPageBtn");
const resultsHost = document.getElementById("resultsHost");
const resultSummary = document.getElementById("resultSummary");

let page = 1;
let total = 0;

function stageName(stage) {
  return {rough: "粗筛", fine: "精筛", sample: "抽样", label: "标签"}[stage] || stage;
}

function sampleLine(row) {
  const sample = row.sample || {};
  if (!Object.keys(sample).length) return "未抽样";
  const sampled = sample.sampled ? "已入样" : "未入样";
  return `${sampled} · v${sample.sample_version || 0}${sample.sample_bucket ? ` · ${sample.sample_bucket}` : ""}`;
}

function stageRecordLine(row) {
  const record = row.stage_record || {};
  if (!Object.keys(record).length) return "无当前阶段记录";
  return `${record.status || "record"}${record.username ? ` · ${record.username}` : ""}`;
}

export function renderRows(rows) {
  resultsHost.innerHTML = "";
  if (!rows.length) {
    resultsHost.innerHTML = '<section class="panel emptyState">暂无结果</section>';
    return;
  }
  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "resultRow panel";
    card.innerHTML = `
      <div class="resultRowHead">
        <div>
          <strong>#${row.order_rank + 1} · ${row.item_id}</strong>
          <p>${row.src_image} → ${row.dst_image}</p>
        </div>
        <div class="resultMeta">
          <span>${sampleLine(row)}</span>
          <span>${stageRecordLine(row)}</span>
        </div>
      </div>
      <div class="resultRowBody">
        <div class="imagePair"></div>
        <div class="readonlyPanel resultReadonly">
          <div class="readonlyHost"></div>
        </div>
      </div>
    `;
    renderImagePair(card.querySelector(".imagePair"), row);
    renderReadonlyAnnotation(card.querySelector(".readonlyHost"), row.annotation_context || {});
    resultsHost.appendChild(card);
  }
}

function updatePager() {
  const pageSize = Number(pageSizeSelect.value || 50);
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  prevPageBtn.disabled = page <= 1;
  nextPageBtn.disabled = page >= pageCount;
  resultSummary.textContent = `${stageName(stageSelect.value)} · 第 ${page} / ${pageCount} 页 · ${total} 条`;
}

export async function loadResults() {
  resultsHost.innerHTML = '<section class="panel loadingPanel">加载中</section>';
  try {
    const payload = await getVisualizationResults(context.datasetId, {
      stage: stageSelect.value,
      page,
      pageSize: Number(pageSizeSelect.value || 50)
    });
    total = payload.total || 0;
    renderRows(payload.rows || []);
    updatePager();
  } catch (error) {
    resultsHost.innerHTML = `<section class="panel messageLine" data-kind="error">${error.body?.error || error.message}</section>`;
  }
}

stageSelect.value = context.stage || "rough";
pageSizeSelect.value = "10";
stageSelect.addEventListener("change", () => {
  page = 1;
  const params = new URLSearchParams(window.location.search);
  params.set("stage", stageSelect.value);
  window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  loadResults();
});
pageSizeSelect.addEventListener("change", () => {
  page = 1;
  loadResults();
});
prevPageBtn.addEventListener("click", () => {
  if (page > 1) {
    page -= 1;
    loadResults();
  }
});
nextPageBtn.addEventListener("click", () => {
  page += 1;
  loadResults();
});

loadResults();
