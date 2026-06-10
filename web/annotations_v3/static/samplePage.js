import {getSampleBuckets, runSample} from "./core/apiClient.js";
import {readDatasetContext} from "./core/datasetContext.js";

const root = document.getElementById("sample-app");
const context = readDatasetContext(root);
const loginView = document.getElementById("loginView");
const appView = document.getElementById("appView");
const topbar = document.querySelector(".topbar");
const loginForm = document.getElementById("loginForm");
const usernameInput = document.getElementById("loginUsernameInput");
const sessionLine = document.getElementById("sessionLine");
const logoutBtn = document.getElementById("logoutBtn");
const pathsInput = document.getElementById("pathsInput");
const perBucketInput = document.getElementById("perBucketInput");
const seedInput = document.getElementById("seedInput");
const sampleStatus = document.getElementById("sampleStatus");
const sampleMessage = document.getElementById("sampleMessage");
const bucketSummary = document.getElementById("bucketSummary");
const bucketRows = document.getElementById("bucketRows");
const loadBucketsBtn = document.getElementById("loadBucketsBtn");
const runSampleBtn = document.getElementById("runSampleBtn");

function selectedPathStrings() {
  return String(pathsInput.value || "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function selectedLabelPaths() {
  return selectedPathStrings().map((entry) => entry.split("/").filter(Boolean));
}

function setMessage(message, kind = "info") {
  sampleMessage.textContent = message;
  sampleMessage.dataset.kind = kind;
}

function renderBuckets(payload) {
  const buckets = payload.buckets || [];
  bucketSummary.textContent = `${buckets.length} buckets`;
  bucketRows.innerHTML = buckets.length
    ? buckets.map((bucket) => `
      <div class="bucketRow">
        <span>${bucket.bucket}</span>
        <strong>${bucket.sampled_count} / ${bucket.count}</strong>
      </div>
    `).join("")
    : '<p class="mutedText">暂无桶统计</p>';
}

export async function loadBuckets() {
  sampleStatus.textContent = "加载桶统计";
  try {
    const payload = await getSampleBuckets(context.datasetId, selectedPathStrings());
    renderBuckets(payload);
    sampleStatus.textContent = "桶统计已更新";
    setMessage("");
  } catch (error) {
    sampleStatus.textContent = "加载失败";
    setMessage(error.body?.error || error.message, "error");
  }
}

export async function runSampling() {
  runSampleBtn.disabled = true;
  sampleStatus.textContent = "抽样中";
  try {
    const result = await runSample(context.datasetId, {
      username: context.username,
      selected_label_paths: selectedLabelPaths(),
      per_bucket: Number(perBucketInput.value || 1),
      seed: String(seedInput.value || "").trim() || null
    });
    sampleStatus.textContent = `sample v${result.sample_version}`;
    setMessage(`已抽样 ${result.sampled_count} 条，释放 ${result.released_label_assignments} 个 label block`, "success");
    await loadBuckets();
  } catch (error) {
    sampleStatus.textContent = "抽样失败";
    setMessage(error.body?.error || error.message, "error");
  } finally {
    runSampleBtn.disabled = false;
  }
}

function showApp(username) {
  context.username = username;
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
  topbar.classList.remove("hidden");
  sessionLine.textContent = `当前用户：${username}`;
  loadBuckets();
}

function showLogin() {
  loginView.classList.remove("hidden");
  appView.classList.add("hidden");
  topbar.classList.add("hidden");
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const clean = String(usernameInput.value || "").trim() || "anonymous";
  window.localStorage.setItem("annotations_v3_username", clean);
  showApp(clean);
});

logoutBtn.addEventListener("click", () => {
  window.localStorage.removeItem("annotations_v3_username");
  showLogin();
});

loadBucketsBtn.addEventListener("click", () => loadBuckets());
runSampleBtn.addEventListener("click", () => runSampling());
pathsInput.addEventListener("change", () => loadBuckets());

const existing = window.localStorage.getItem("annotations_v3_username");
if (existing) showApp(existing);
else showLogin();
