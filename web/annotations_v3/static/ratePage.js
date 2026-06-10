import {claimAssignment, saveAnnotationPatch} from "./core/apiClient.js";
import {readDatasetContext} from "./core/datasetContext.js";
import {createImageAssetService} from "./core/imageAssetService.js";
import {createPreloadScheduler} from "./core/preloadScheduler.js";
import {createDraftStore} from "./core/annotationDraftStore.js";
import {buildPatchPayload} from "./core/annotationPayload.js";
import {renderImagePair} from "./components/imagePairView.js";
import {renderField} from "./components/annotationFieldRenderer.js";
import {renderReadonlyAnnotation} from "./components/readonlyAnnotationView.js";

const root = document.getElementById("rate-app");
const pageRoot = document.body;
const context = readDatasetContext(root);
const loginView = document.getElementById("loginView");
const appView = document.getElementById("appView");
const topbar = document.querySelector(".topbar");
const loginForm = document.getElementById("loginForm");
const usernameInput = document.getElementById("loginUsernameInput");
const sessionLine = document.getElementById("sessionLine");
const logoutBtn = document.getElementById("logoutBtn");
const workTitle = document.getElementById("workTitle");
const workProgress = document.getElementById("workProgress");
const emptyStage = document.getElementById("emptyStage");
const stageBody = document.getElementById("stageBody");
const imagePairHost = document.getElementById("imagePairHost");
const stageForm = document.getElementById("stageForm");
const readonlyHost = document.getElementById("readonlyHost");
const saveMessage = document.getElementById("saveMessage");
const nextBtn = document.getElementById("nextBtn");
const prevBtn = document.getElementById("prevBtn");
const imageService = createImageAssetService();
const scheduler = createPreloadScheduler(imageService);
let assignment = null;
let items = [];
let currentIndex = 0;
let store = null;
let openedAt = Date.now() / 1000;

function stageName(stage) {
  return {rough: "粗筛", fine: "精筛", label: "标签"}[stage] || stage;
}

function showMessage(message, kind = "info") {
  saveMessage.textContent = message;
  saveMessage.dataset.kind = kind;
}

function renderFieldControls() {
  stageForm.innerHTML = "";
  for (const field of store.fields) {
    if (!field.readonly) renderField(stageForm, field, store);
  }
}

function showApp(username) {
  context.username = username;
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
  topbar.classList.remove("hidden");
  sessionLine.textContent = `当前用户：${username}`;
  workTitle.textContent = `${stageName(context.stage)} · ${context.datasetId}`;
  loadAssignment().catch((error) => {
    emptyStage.textContent = `加载失败：${error.body?.error || error.message}`;
    emptyStage.classList.remove("hidden");
    stageBody.classList.add("hidden");
  });
}

function showLogin() {
  loginView.classList.remove("hidden");
  appView.classList.add("hidden");
  topbar.classList.add("hidden");
}

export async function loadAssignment() {
  const payload = await claimAssignment(context.datasetId, context.stage, context.username);
  assignment = payload.assignment;
  items = payload.items || [];
  currentIndex = Math.min(currentIndex, Math.max(0, items.length - 1));
  renderCurrentItem();
}

export function renderCurrentItem() {
  if (!assignment || !items.length) {
    emptyStage.textContent = "暂无可领取内容";
    emptyStage.classList.remove("hidden");
    stageBody.classList.add("hidden");
    return;
  }
  emptyStage.classList.add("hidden");
  stageBody.classList.remove("hidden");
  const item = items[currentIndex];
  openedAt = Date.now() / 1000;
  store = createDraftStore(item.annotation_context || {});
  workProgress.textContent = `${currentIndex + 1} / ${items.length} · ${assignment.assignment_id}`;
  renderImagePair(imagePairHost, item);
  renderFieldControls();
  renderReadonlyAnnotation(readonlyHost, item.annotation_context || {});
  scheduler.scheduleRatePreloads(items, currentIndex, {rtt_ms: navigator.connection?.rtt, downlink_mbps: navigator.connection?.downlink});
  showMessage("");
}

export async function saveCurrentItem() {
  if (!assignment || !items[currentIndex] || !store) return;
  const item = items[currentIndex];
  const payload = buildPatchPayload({
    assignmentId: assignment.assignment_id,
    stage: assignment.stage,
    username: context.username,
    baseVersion: item.annotation_context?.version,
    changes: store.changes(),
    openedAt
  });
  try {
    const result = await saveAnnotationPatch(context.datasetId, item.item_id, payload);
    item.annotation_context = result.annotation_context;
    assignment = result.assignment;
    if (currentIndex < items.length - 1) {
      currentIndex += 1;
      renderCurrentItem();
    } else {
      showMessage("当前块已完成", "success");
    }
  } catch (error) {
    showMessage(error.body?.code || error.message, "error");
  }
}

function previousItem() {
  if (currentIndex > 0) {
    currentIndex -= 1;
    renderCurrentItem();
  }
}

function handleKeydown(event) {
  const tag = event.target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (/^[1-5]$/.test(event.key) && store) {
    const mos = store.fields.find((field) => field.field_id === "quality.mos");
    if (mos) {
      store.setValue(mos, Number(event.key));
      renderFieldControls();
    }
  }
  if (event.code === "Space") {
    event.preventDefault();
    saveCurrentItem();
  }
}

document.addEventListener("keydown", handleKeydown);
nextBtn.addEventListener("click", () => saveCurrentItem());
prevBtn.addEventListener("click", () => previousItem());
loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const clean = String(usernameInput.value || "").trim() || "anonymous";
  window.localStorage.setItem("annotations_v3_username", clean);
  showApp(clean);
});
logoutBtn.addEventListener("click", () => {
  window.localStorage.removeItem("annotations_v3_username");
  assignment = null;
  items = [];
  currentIndex = 0;
  showLogin();
});

const existing = window.localStorage.getItem("annotations_v3_username");
if (existing) showApp(existing);
else showLogin();
