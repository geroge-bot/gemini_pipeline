import {requestJson} from "./core/apiClient.js";
import {readDatasetContext} from "./core/datasetContext.js";

const root = document.getElementById("imports-app");
const context = readDatasetContext(root);
const pathInput = document.getElementById("importPathInput");
const mergePolicySelect = document.getElementById("mergePolicySelect");
const stagePolicySelect = document.getElementById("stagePolicySelect");
const validateImportBtn = document.getElementById("validateImportBtn");
const commitImportBtn = document.getElementById("commitImportBtn");
const importStatus = document.getElementById("importStatus");
const importMessage = document.getElementById("importMessage");
const reportRows = document.getElementById("reportRows");
const errorsLink = document.getElementById("errorsLink");

function payload() {
  return {
    path: String(pathInput.value || "").trim(),
    merge_policy: mergePolicySelect.value,
    stage_record_policy: stagePolicySelect.value
  };
}

function setMessage(message, kind = "info") {
  importMessage.textContent = message;
  importMessage.dataset.kind = kind;
}

function row(label, value) {
  return `<div class="bucketRow"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderReport(report) {
  importStatus.textContent = `${report.status} · ${report.import_id}`;
  reportRows.innerHTML = [
    row("total_rows", report.total_rows),
    row("matched_rows", report.matched_rows),
    row("updated_items", report.updated_items),
    row("unchanged_rows", report.unchanged_rows),
    row("unmatched_rows", report.unmatched_rows),
    row("accepted_labels", report.accepted_labels),
    row("rejected_labels", report.rejected_labels),
    row("warnings", report.warnings?.length || 0),
    row("errors", report.errors?.length || 0)
  ].join("");
  errorsLink.href = `/api/datasets/${context.datasetId}/imports/${report.import_id}/errors`;
  errorsLink.classList.remove("hidden");
}

async function submitImport(mode) {
  const body = payload();
  if (!body.path) {
    setMessage("请输入 JSONL path", "error");
    return;
  }
  validateImportBtn.disabled = true;
  commitImportBtn.disabled = true;
  importStatus.textContent = mode === "dry_run" ? "dry-run 中" : "commit 中";
  try {
    const report = await requestJson(
      mode === "dry_run"
        ? `/api/datasets/${context.datasetId}/imports/validate`
        : `/api/datasets/${context.datasetId}/imports`,
      {method: "POST", body: JSON.stringify(body)}
    );
    renderReport(report);
    setMessage(mode === "dry_run" ? "dry-run 完成" : "commit 完成", "success");
  } catch (error) {
    importStatus.textContent = "导入失败";
    setMessage(error.body?.error || error.message, "error");
  } finally {
    validateImportBtn.disabled = false;
    commitImportBtn.disabled = false;
  }
}

validateImportBtn.addEventListener("click", () => submitImport("dry_run"));
commitImportBtn.addEventListener("click", () => submitImport("commit"));
