const state = {
  username: localStorage.getItem("annotations_v2.username") || "",
  page: document.body.dataset.page || "home",
  taskId: document.body.dataset.taskId || "",
  editingTaskId: "",
  tasks: [],
  activeTask: null,
  stage: "rough",
  items: [],
  index: 0,
  rateOffset: 0,
  rateTotal: 0,
  rateHistory: [],
  visualizationResults: [],
  visualizationPage: 0,
  visualizationTotal: 0,
  visualizationFilters: { statuses: [], mos: [], has_defect: [], annotators: [], labels: {} },
  visualizationFilterOptions: { statuses: [], mos: [], has_defect: [], annotators: [], label_options: [] },
  sampleBuckets: [],
  sampleCandidateCount: 0,
};

const TASK_DELETE_ADMIN_USERNAME = "孙本猿";
const RATE_PAGE_SIZE = 1;
const PRELOAD_FORWARD_PAGES = 3;
const MAX_PRELOADED_IMAGES = 48;
const preloadedImages = new Map();
let ratePagingInFlight = false;

const $ = (id) => document.getElementById(id);

function showToast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  setTimeout(() => node.classList.add("hidden"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inlineMarkdown(value) {
  return String(value || "")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdown(value) {
  const lines = escapeHtml(value).replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let list = [];
  let codeBlock = [];
  let inCodeBlock = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    html.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
    list = [];
  };
  const flushCodeBlock = () => {
    if (!codeBlock.length) return;
    html.push(`<pre><code>${codeBlock.join("\n")}</code></pre>`);
    codeBlock = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCodeBlock) {
        flushCodeBlock();
        inCodeBlock = false;
      } else {
        flushParagraph();
        flushList();
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeBlock.push(line);
      continue;
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(line.trim());
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(4, heading[1].length + 2);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }

  flushCodeBlock();
  flushParagraph();
  flushList();
  return html.join("") || '<p class="metaText">暂无内容</p>';
}

function renderGenerationPromptDisclosure(item) {
  const prompt = String(item?.generation_prompt || "").trim();
  const promptPath = String(item?.generation_prompt_json_path || "").trim();
  const title = promptPath ? ` title="${escapeHtml(promptPath)}"` : "";
  const body = prompt
    ? `<div class="markdownBody">${renderMarkdown(prompt)}</div>`
    : '<div class="metaText promptEmpty">未找到 prompt</div>';
  return `
    <details class="promptDisclosure"${title}>
      <summary>生图 Prompt</summary>
      ${body}
    </details>
  `;
}

function renderImagePrompt(item) {
  if ($("imagePromptHost")) $("imagePromptHost").innerHTML = renderGenerationPromptDisclosure(item);
}

function renderVisualizationImagePrompt(item) {
  if ($("visualizationPromptHost")) $("visualizationPromptHost").innerHTML = renderGenerationPromptDisclosure(item);
}

function updateSession() {
  if ($("loginUsernameInput")) $("loginUsernameInput").value = state.username;
  $("sessionLine").textContent = state.username ? `当前用户：${state.username}` : "未登录";
}

function showLogin() {
  $("loginView").classList.remove("hidden");
  $("appView").classList.add("hidden");
  document.querySelector(".topbar")?.classList.add("hidden");
  if ($("loginUsernameInput")) $("loginUsernameInput").value = state.username;
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  document.querySelector(".topbar")?.classList.remove("hidden");
  updateSession();
}

async function enterApp() {
  if (!state.username) {
    showLogin();
    return;
  }
  showApp();
  if (state.page === "rate") {
    const params = new URLSearchParams(window.location.search);
    await loadTask(state.taskId);
    await openStage(state.taskId, params.get("stage") || "rough");
    return;
  }
  if (state.page === "visualize") {
    await loadTask(state.taskId);
    await openVisualizationPage();
    return;
  }
  await loadTasks();
  if (state.page === "sample") {
    await openSamplePage();
    return;
  }
}

function parseList(value) {
  return String(value || "")
    .replaceAll("\n", ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseLabelPaths(value) {
  return parseList(value)
    .map((path) => path.split("/").map((part) => part.trim()).filter(Boolean))
    .filter((path) => path.length > 0);
}

async function loadTasks(options = {}) {
  const refreshSummaries = options.refreshSummaries !== false;
  const data = await api("/api/tasks");
  state.tasks = data.tasks || [];
  if ($("taskList")) renderTasks();
  if (refreshSummaries) refreshTaskSummaries();
}

async function loadTask(taskId) {
  const data = await api(`/api/tasks/${taskId}`);
  const task = data.task;
  if (!task) throw new Error("任务不存在或尚未加载");
  const index = state.tasks.findIndex((entry) => entry.id === task.id);
  if (index === -1) {
    state.tasks.push(task);
  } else {
    state.tasks[index] = task;
  }
  state.activeTask = task;
  return task;
}

function refreshTaskSummaries() {
  if (!$("taskList") || !state.tasks.length) return;
  for (const task of state.tasks) {
    refreshTaskSummary(task.id).catch((error) => console.warn(error));
  }
}

async function refreshTaskSummary(taskId) {
  const data = await api(`/api/tasks/${taskId}/summary`);
  const task = taskById(taskId);
  if (!task) return;
  task.summary = data.summary || task.summary || {};
  renderTasks();
}

function renderTasks() {
  $("taskCount").textContent = `${state.tasks.length} 个`;
  const list = $("taskList");
  list.innerHTML = "";
  if (!state.tasks.length) {
    list.innerHTML = '<div class="emptyBox">暂无任务</div>';
    return;
  }
  for (const task of state.tasks) {
    const summary = task.summary || {};
    const card = document.createElement("article");
    card.className = "taskCard";
    card.innerHTML = `
      <div class="taskCardHead">
        <h3>${escapeHtml(task.name)}</h3>
        <span>${summary.total || 0} 条</span>
      </div>
      <div class="progressGrid">
        ${screeningProgressCells("粗筛", summary.rough_rounds, summary.rough_passed, { href: `/dataset/rate/${task.id}?stage=rough` })}
        ${screeningProgressCells("精筛", summary.fine_rounds, summary.fine_passed, { href: `/dataset/rate/${task.id}?stage=fine` })}
        ${progressCell("采样", summary.sampled, summary.fine_passed, null, { href: `/dataset/sample/${task.id}` })}
        ${progressCell("标签纠错", summary.label_completed, summary.sampled, null, { href: `/dataset/rate/${task.id}?stage=label` })}
      </div>
      <div class="pathLine">${escapeHtml(task.jsonl_path)}</div>
      <div class="taskActions">
        <a class="buttonLike ghost" href="${`/dataset/visualize/${task.id}`}">结果展示</a>
        <button class="ghost" data-action="edit" data-id="${task.id}" type="button">编辑</button>
        <button class="ghost" data-action="import" data-id="${task.id}" type="button">导入</button>
        <button class="ghost" data-action="cache-previews" data-id="${task.id}" type="button">缓存图片</button>
        <a class="buttonLike ghost" href="/api/tasks/${task.id}/download">导出</a>
        ${deleteTaskButton(task)}
      </div>
    `;
    list.appendChild(card);
  }
}

function canDeleteTasks() {
  return state.username === TASK_DELETE_ADMIN_USERNAME;
}

function deleteTaskButton(task) {
  if (!canDeleteTasks()) return "";
  return `<button class="dangerGhost" data-action="delete" data-id="${task.id}" data-name="${escapeHtml(task.name)}" type="button">删除</button>`;
}

function progressCell(label, done, total, passed = null, entry = null) {
  const safeDone = Number(done || 0);
  const safeTotal = Number(total || 0);
  const percent = safeTotal ? Math.round((safeDone / safeTotal) * 100) : 0;
  const passedText = passed === null || passed === undefined ? "" : ` · 通过 ${passed}`;
  const content = `
      <span>${label}</span>
      <strong>${safeDone}/${safeTotal}</strong>
      <div class="meter"><i style="width:${percent}%"></i></div>
      <small>${percent}%${passedText}</small>
  `;
  if (entry?.href) {
    return `<a href="${entry.href}" class="progressCell progressEntry">${content}</a>`;
  }
  if (entry?.action && entry?.id) {
    return `<button class="progressCell progressEntry" data-action="${entry.action}" data-id="${entry.id}" type="button">${content}</button>`;
  }
  return `<div class="progressCell">${content}</div>`;
}

function screeningProgressCells(label, rounds, passed = null, entry = null) {
  const values = Array.isArray(rounds) && rounds.length ? rounds : [{ round: 1, completed: 0, total: 0 }];
  return values.map((round) => {
    const passedText = Number(round.round) === values.length ? passed : null;
    return progressCell(`${label} 第 ${round.round} 人`, round.completed, round.total, passedText, entry);
  }).join("");
}

async function createTask(event) {
  event.preventDefault();
  const payload = {
    name: $("taskNameInput").value.trim(),
    root_dir: $("rootDirInput").value.trim(),
    jsonl_path: $("jsonlPathInput").value.trim(),
    label_dir: $("labelDirInput").value.trim(),
    generation_prompt_dir: $("generationPromptDirInput").value.trim(),
    selected_label_paths: parseLabelPaths($("labelPathsInput").value),
    rough: {
      min_mos: Number($("roughMinMosInput").value || 4),
      annotator_count: Number($("roughAnnotatorCountInput").value || 1),
      require_no_defect: true,
      issue_options: parseList($("issueOptionsInput").value),
    },
    fine: {
      min_mos: Number($("fineMinMosInput").value || 4),
      annotator_count: Number($("fineAnnotatorCountInput").value || 1),
      enable_defect: $("fineDefectInput").checked,
    },
  };
  await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
  $("createTaskForm").reset();
  $("roughMinMosInput").value = 4;
  $("fineMinMosInput").value = 4;
  $("roughAnnotatorCountInput").value = 1;
  $("fineAnnotatorCountInput").value = 1;
  showToast("任务已创建");
  await loadTasks();
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function warmPreviewCache(taskId, button) {
  const originalText = button?.textContent || "缓存图片";
  if (button) {
    button.disabled = true;
    button.textContent = "缓存中 0%";
  }
  try {
    const data = await api(`/api/tasks/${taskId}/preview-cache/jobs`, { method: "POST" });
    const job = await waitForPreviewCacheJob(taskId, data.job.id, button);
    const result = job.result || {};
    showToast(`图片缓存完成：生成 ${result.generated_count || 0}，跳过 ${result.skipped_count || 0}，失败 ${result.failed_count || 0}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function waitForPreviewCacheJob(taskId, jobId, button) {
  while (true) {
    const data = await api(`/api/tasks/${taskId}/preview-cache/jobs/${jobId}`);
    const job = data.job;
    if (button) {
      button.textContent = `缓存中 ${job.progress || 0}%`;
    }
    if (job.status === "completed") return job;
    if (job.status === "failed") throw new Error(job.error || "图片缓存失败");
    await sleep(350);
  }
}

async function importTaskAnnotations(taskId) {
  const jsonlPath = window.prompt("导入 JSONL 文件路径");
  if (!jsonlPath || !jsonlPath.trim()) return;
  const data = await api(`/api/tasks/${taskId}/import`, {
    method: "POST",
    body: JSON.stringify({ jsonl_path: jsonlPath.trim() }),
  });
  const result = data.result || {};
  showToast(`导入完成：更新 ${result.imported_count || 0} 条，跳过 ${result.skipped_count || 0} 条，未匹配 ${result.unmatched_count || 0} 条`);
  await loadTasks();
}

async function deleteTask(taskId, taskName) {
  const confirmed = window.confirm(`确定删除任务“${taskName || taskId}”吗？\n\n只会从任务列表移除，不会删除已有标注结果或原始数据。`);
  if (!confirmed) return;
  await api(`/api/tasks/${taskId}`, {
    method: "DELETE",
    body: JSON.stringify({ username: state.username }),
  });
  showToast("任务已从列表移除");
  await loadTasks();
}

function taskIssueOptionsText(task) {
  return (task?.rough?.issue_options || []).join("\n");
}

function taskLabelPathsText(task) {
  return (task?.selected_label_paths || []).map((path) => (path || []).join("/")).join("\n");
}

function taskGenerationPromptDirText(task) {
  return task?.generation_prompt_dir || "";
}

function openEditTaskDialog(taskId) {
  const task = taskById(taskId);
  if (!task) {
    showToast("任务不存在或尚未加载");
    return;
  }
  state.editingTaskId = taskId;
  $("editTaskTitle").textContent = `编辑任务：${task.name || taskId}`;
  $("editIssueOptionsInput").value = taskIssueOptionsText(task);
  $("editLabelPathsInput").value = taskLabelPathsText(task);
  $("editGenerationPromptDirInput").value = taskGenerationPromptDirText(task);
  $("taskEditOverlay").classList.remove("hidden");
  $("taskEditDialog").classList.remove("hidden");
  $("editIssueOptionsInput").focus();
}

function closeEditTaskDialog() {
  state.editingTaskId = "";
  $("taskEditOverlay")?.classList.add("hidden");
  $("taskEditDialog")?.classList.add("hidden");
}

async function saveTaskEdits(event) {
  event.preventDefault();
  if (!state.editingTaskId) return;
  await api(`/api/tasks/${state.editingTaskId}`, {
    method: "PATCH",
    body: JSON.stringify({
      rough: { issue_options: parseList($("editIssueOptionsInput").value) },
      selected_label_paths: parseLabelPaths($("editLabelPathsInput").value),
      generation_prompt_dir: $("editGenerationPromptDirInput").value.trim(),
    }),
  });
  closeEditTaskDialog();
  showToast("任务已更新");
  await loadTasks();
}

function taskById(taskId) {
  return state.tasks.find((task) => task.id === taskId) || null;
}

function stageItemsUrl(taskId, stage, includeHistory = false, options = {}) {
  const params = new URLSearchParams({
    stage,
    username: state.username,
  });
  if (includeHistory) {
    params.set("include_history", "1");
  }
  if (options.offset !== undefined) {
    params.set("offset", String(options.offset));
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  return `/api/tasks/${taskId}/items?${params.toString()}`;
}

function itemHasCurrentUserAnnotation(item) {
  const record = item?.record || {};
  if (["rough", "fine"].includes(state.stage)) {
    return Boolean(record[state.stage]?.username);
  }
  if (state.stage === "label") {
    return record.label?.username === state.username;
  }
  return false;
}

function firstUnannotatedItemIndex() {
  const index = state.items.findIndex((item) => !itemHasCurrentUserAnnotation(item));
  return index === -1 ? 0 : index;
}

async function openStage(taskId, stage) {
  state.activeTask = taskById(taskId) || state.activeTask;
  if (!state.activeTask) {
    showToast("任务不存在或尚未加载");
    return;
  }
  state.stage = stage;
  state.index = 0;
  state.rateOffset = 0;
  state.rateTotal = 0;
  state.rateHistory = [];
  $("samplePanel")?.classList.add("hidden");
  $("workbench")?.classList.remove("hidden");
  await loadRateItemPage(0);
}

async function loadRateItemPage(offset = 0) {
  if (!state.activeTask) return;
  const data = await api(stageItemsUrl(state.activeTask.id, state.stage, false, {
    offset,
    limit: RATE_PAGE_SIZE,
  }));
  state.items = data.items || [];
  state.index = 0;
  state.rateOffset = Number(data.offset || 0);
  state.rateTotal = Number(data.total || 0);
  renderCurrentItem();
}

function renderCurrentItem() {
  const task = state.activeTask;
  const stageName = stageTitle(state.stage);
  $("workTitle").textContent = task ? `${task.name} · ${stageName}` : stageName;
  $("workProgress").textContent = state.items.length ? `${Math.min(state.rateOffset + 1, state.rateTotal)}/${state.rateTotal}` : "0/0";
  $("emptyStage").classList.toggle("hidden", state.items.length > 0);
  $("stageBody").classList.toggle("hidden", state.items.length === 0);
  if (!state.items.length) {
    renderImagePrompt(null);
    return;
  }

  const item = state.items[state.index];
  preparePreviewImage($("srcImage"), item.image_urls.src);
  preparePreviewImage($("dstImage"), item.image_urls.dst);
  $("srcImage").onerror = () => $("srcImage").removeAttribute("src");
  $("dstImage").onerror = () => $("dstImage").removeAttribute("src");
  renderImagePrompt(item);
  renderStageForm(item);
  preloadStageNeighbors();
}

function stageTitle(stage) {
  return { rough: "粗筛", fine: "精筛", label: "标签纠错" }[stage] || "阶段";
}

function renderStageForm(item) {
  const form = $("stageForm");
  const record = item.record || {};
  if (state.stage === "rough") {
    const current = record.rough || {};
    form.innerHTML = `
      <h2>粗筛</h2>
      ${mosField(current.mos)}
      ${defectField(current.has_defect)}
      ${issueCheckboxes(current.issues || [])}
    `;
    return;
  }
  if (state.stage === "fine") {
    const current = fineDefaultRecord(record);
    form.innerHTML = `
      <h2>精筛</h2>
      ${mosField(current.mos)}
      ${defectField(current.has_defect)}
      ${issueCheckboxes(current.issues || [])}
    `;
    return;
  }
  const currentLabels = labelDraftLabels(item);
  form.innerHTML = `
    <h2>标签纠错</h2>
    <div id="labelEditor" class="labelEditor">${labelChoiceInputs(currentLabels)}</div>
    <button type="submit">保存标签</button>
  `;
}

function fineDefaultRecord(record) {
  return record.fine || record.rough || {};
}

function mosField(value) {
  const selectedValue = Number(value || 0);
  const options = [1, 2, 3, 4, 5].map((score) => {
    const checked = selectedValue === score ? "checked" : "";
    return `
      <label class="scoreOption">
        <input name="mosOption" type="radio" value="${score}" ${checked}>
        <span>${score}</span>
      </label>
    `;
  }).join("");
  return `
    <fieldset class="mosScoreBar" id="mosInput">
      <legend>MOS 分</legend>
      ${options}
    </fieldset>
  `;
}

function defectField(value) {
  const hasDefect = Boolean(value);
  return `
    <fieldset class="defectRadioGroup" id="defectInput">
      <legend>是否有瑕疵</legend>
      <label class="radioPill">
        <input name="defectOption" type="radio" value="false" ${hasDefect ? "" : "checked"}>
        <span>否</span>
      </label>
      <label class="radioPill">
        <input name="defectOption" type="radio" value="true" ${hasDefect ? "checked" : ""}>
        <span>是（E）</span>
      </label>
    </fieldset>
  `;
}

function issueOptions() {
  return state.activeTask?.rough?.issue_options || [];
}

function issueCheckboxes(selectedValues) {
  const selected = new Set(selectedValues || []);
  const options = issueOptions();
  if (!options.length) return "";
  return `
    <fieldset>
      <legend>问题项</legend>
      ${options.map((option) => `
        <label class="checkboxRow">
          <input name="issueOption" type="checkbox" value="${escapeHtml(option)}" ${selected.has(option) ? "checked" : ""}>
          <span>${escapeHtml(option)}</span>
        </label>
      `).join("")}
    </fieldset>
  `;
}

function pickSelectedLabels(labels) {
  const result = {};
  for (const path of labelCorrectionPaths()) {
    const value = getNested(labels, path);
    if (value !== undefined && value !== null) setNested(result, path, value);
  }
  return result;
}

function labelDraftLabels(item) {
  const record = item.record || {};
  const draftLabels = record.label_draft?.labels || pickSelectedLabels(item.labels || {});
  return mergeLabelObjects(draftLabels, record.label?.labels || {});
}

function mergeLabelObjects(baseLabels, overrideLabels) {
  const result = JSON.parse(JSON.stringify(baseLabels || {}));
  overlayLabels(result, overrideLabels || {});
  return result;
}

function overlayLabels(target, source) {
  for (const [key, value] of Object.entries(source || {})) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      if (!target[key] || typeof target[key] !== "object" || Array.isArray(target[key])) target[key] = {};
      overlayLabels(target[key], value);
    } else {
      target[key] = value;
    }
  }
}

function labelCorrectionPaths() {
  const selected = state.activeTask?.selected_label_paths || [];
  if (selected.length) return selected;
  const groups = state.activeTask?.label_option_groups || [];
  return groups.flatMap((group) => (group.dimensions || []).map((dimension) => [group.name, dimension.name]));
}

function labelOptionsForPath(path) {
  const [groupName, ...dimensionParts] = path;
  const dimensionName = dimensionParts.join("/");
  const group = (state.activeTask?.label_option_groups || []).find((entry) => entry.name === groupName);
  const dimension = (group?.dimensions || []).find((entry) => entry.name === dimensionName);
  return Array.isArray(dimension?.options) ? dimension.options : [];
}

function labelChoiceInputs(labels) {
  const paths = labelCorrectionPaths();
  if (!paths.length) return '<div class="emptyBox">暂无可纠错标签</div>';
  return paths.map((path) => renderLabelChoiceGroup(path, getNested(labels, path))).join("");
}

function renderLabelChoiceGroup(path, currentValue) {
  const options = labelOptionsForPath(path);
  const fieldset = document.createElement("fieldset");
  fieldset.className = "filterGroup labelChoiceGroup";
  fieldset.innerHTML = `<legend>${escapeHtml(path.join("/"))}</legend>`;
  const list = document.createElement("div");
  list.className = "labelOptions";
  if (!options.length) {
    list.innerHTML = '<span class="emptyFilterOption">暂无可选项</span>';
  }
  for (const option of options) {
    const label = document.createElement("label");
    label.className = "labelOption labelChoiceOption";
    const input = document.createElement("input");
    input.type = "radio";
    input.className = "labelChoiceInput";
    input.name = `labelChoice:${JSON.stringify(path)}`;
    input.dataset.labelPath = JSON.stringify(path);
    input.dataset.optionValue = JSON.stringify(option);
    input.value = String(option);
    input.checked = String(currentValue) === String(option);
    if (input.checked) input.setAttribute("checked", "checked");
    const span = document.createElement("span");
    span.textContent = String(option);
    label.appendChild(input);
    label.appendChild(span);
    list.appendChild(label);
  }
  fieldset.appendChild(list);
  return fieldset.outerHTML;
}

function getNested(value, path) {
  let cursor = value;
  for (const part of path) {
    if (!cursor || typeof cursor !== "object" || !(part in cursor)) return undefined;
    cursor = cursor[part];
  }
  return cursor;
}

function setNested(target, path, value) {
  let cursor = target;
  for (const part of path.slice(0, -1)) {
    if (!cursor[part] || typeof cursor[part] !== "object") cursor[part] = {};
    cursor = cursor[part];
  }
  if (path.length) cursor[path[path.length - 1]] = value;
}

function isTextEditingShortcutTarget(target) {
  const tagName = target?.tagName;
  if (target?.isContentEditable || tagName === "TEXTAREA" || tagName === "SELECT") return true;
  if (tagName !== "INPUT") return false;
  const inputType = String(target.getAttribute("type") || "text").toLowerCase();
  return !["radio", "checkbox"].includes(inputType);
}

function isScreeningStage() {
  return state.page === "rate" && ["rough", "fine"].includes(state.stage);
}

function isRatePagingStage() {
  return state.page === "rate" && ["rough", "fine", "label"].includes(state.stage);
}

function selectMosScore(score) {
  const input = document.querySelector(`input[name="mosOption"][value="${score}"]`);
  if (input) {
    input.checked = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function setDefectValue(hasDefect) {
  const value = hasDefect ? "true" : "false";
  const input = document.querySelector(`input[name="defectOption"][value="${value}"]`);
  if (input) {
    input.checked = true;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function collectScreeningPayload() {
  const mos = Number(document.querySelector('input[name="mosOption"]:checked')?.value || 0);
  if (!mos) {
    throw new Error("请选择 MOS 分后再翻页");
  }
  return {
    username: state.username,
    mos,
    has_defect: document.querySelector('input[name="defectOption"]:checked')?.value === "true",
    issues: Array.from(document.querySelectorAll('input[name="issueOption"]:checked')).map((input) => input.value),
  };
}

async function saveCurrentStageBeforePageChange() {
  if (!isRatePagingStage() || !state.items.length) return true;
  if (!state.username) {
    showToast("请先登录");
    return false;
  }
  let payload;
  try {
    payload = collectCurrentStagePayload();
  } catch (error) {
    showToast(error.message);
    return false;
  }
  const item = state.items[state.index];
  const data = await api(`/api/tasks/${state.activeTask.id}/items/${item.item_index}/${state.stage}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  item.record = item.record || {};
  item.record[state.stage] = data.record;
  return true;
}

async function reloadCurrentStageAfterSave(preferredIndex) {
  const taskId = state.activeTask?.id || state.taskId;
  const stage = state.stage;
  await loadTask(taskId);
  state.stage = stage;
  await loadRateItemPage(state.rateOffset);
}

function advanceCurrentStageLocally(preferredIndex) {
  state.index = Math.max(0, Math.min(Math.max(0, state.items.length - 1), preferredIndex));
  renderCurrentItem();
}

function setRatePagingBusy(isBusy) {
  ratePagingInFlight = isBusy;
  if ($("nextBtn")) $("nextBtn").disabled = isBusy;
}

async function goToItem(nextIndex) {
  if (!state.items.length) return;
  if (ratePagingInFlight) return;
  if (nextIndex < 0) {
    const previousItem = state.rateHistory.pop();
    if (!previousItem) return;
    state.items = [previousItem];
    state.index = 0;
    state.rateOffset = Math.max(0, state.rateOffset - 1);
    renderCurrentItem();
    return;
  }
  const boundedIndex = Math.max(0, Math.min(state.items.length - 1, nextIndex));
  const movingPastLastItem = nextIndex > state.index && boundedIndex === state.index;
  if (boundedIndex === state.index && !movingPastLastItem) return;
  if (nextIndex < state.index) {
    state.index = boundedIndex;
    renderCurrentItem();
    return;
  }
  setRatePagingBusy(true);
  try {
    if (!(await saveCurrentStageBeforePageChange())) return;
    state.rateHistory.push(state.items[state.index]);
    await loadRateItemPage(state.rateOffset);
    if (movingPastLastItem) {
      showToast("已保存");
    }
  } finally {
    setRatePagingBusy(false);
  }
}

function goNextItem() {
  goToItem(state.index + 1).catch((error) => showToast(error.message));
}

function handleRateShortcuts(event) {
  if (!isRatePagingStage() || event.repeat || isTextEditingShortcutTarget(event.target)) return;
  if (isScreeningStage() && /^[1-5]$/.test(event.key)) {
    selectMosScore(event.key);
    event.preventDefault();
    return;
  }
  if (isScreeningStage() && event.key.toLowerCase() === "e") {
    setDefectValue(true);
    event.preventDefault();
    return;
  }
  if (event.code === "Space") {
    goToItem(state.index + 1).catch((error) => showToast(error.message));
    event.preventDefault();
  }
}

async function saveStage(event) {
  event.preventDefault();
  if (!state.username) {
    showToast("请先保存用户名");
    return;
  }
  const item = state.items[state.index];
  const payload = collectCurrentStagePayload();
  await api(`/api/tasks/${state.activeTask.id}/items/${item.item_index}/${state.stage}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showToast("已保存");
  await loadRateItemPage(state.rateOffset);
}

function collectCurrentStagePayload() {
  if (state.stage === "label") {
    return { username: state.username, labels: collectLabels() };
  }
  return collectScreeningPayload();
}

function collectLabels() {
  const labels = {};
  document.querySelectorAll(".labelChoiceInput:checked").forEach((input) => {
    const path = JSON.parse(input.dataset.labelPath);
    setNested(labels, path, JSON.parse(input.dataset.optionValue));
  });
  return labels;
}

async function openSamplePage() {
  state.activeTask = taskById(state.taskId);
  if ($("sampleTitle")) {
    $("sampleTitle").textContent = `${state.activeTask?.name || "任务"} · 数据采样`;
  }
  await reloadSampleBuckets();
}

async function reloadSampleBuckets() {
  const data = await api(`/api/tasks/${state.taskId}/sample-buckets`);
  const result = data.result || {};
  state.sampleBuckets = result.buckets || [];
  state.sampleCandidateCount = result.candidate_count || 0;
  renderSampleBuckets(result);
}

function renderSampleBuckets(result) {
  if ($("sampleSummary")) {
    $("sampleSummary").textContent = `精筛通过候选 ${result?.candidate_count || 0} 条，已加入标签纠错 ${result?.sampled_count || 0} 条`;
  }
  const list = $("sampleBucketList");
  if (!list) return;
  if (!state.sampleBuckets.length) {
    list.innerHTML = '<div class="emptyBox">暂无可采样数据</div>';
    return;
  }
  list.innerHTML = state.sampleBuckets.map((bucket, index) => `
    <article class="sampleBucketRow">
      <div>
        <h3>${escapeHtml(bucket.bucket)}</h3>
        <p>${bucket.sampled_count || 0}/${bucket.candidate_count || 0} 已加入标签纠错</p>
      </div>
      <div class="sampleBucketControls">
        <input class="sampleCountInput" data-bucket-index="${index}" type="number" min="0" max="${bucket.candidate_count || 0}" value="0" aria-label="${escapeHtml(bucket.bucket)} 采样数量">
        <button class="ghost" data-sample-all-bucket="${index}" type="button">全选</button>
      </div>
    </article>
  `).join("");
}

function collectSampleSelections() {
  return Array.from(document.querySelectorAll(".sampleCountInput"))
    .map((input) => {
      const bucket = state.sampleBuckets[Number(input.dataset.bucketIndex)];
      return { bucket: bucket?.bucket || "", count: Number(input.value || 0) };
    })
    .filter((selection) => selection.bucket && selection.count > 0);
}

function selectAllSampleBuckets() {
  document.querySelectorAll(".sampleCountInput").forEach((input) => {
    const bucket = state.sampleBuckets[Number(input.dataset.bucketIndex)];
    input.value = bucket?.candidate_count || 0;
  });
}

async function runSample(selectAll = false) {
  if (!state.taskId) return;
  const payload = selectAll ? { select_all: true } : { selections: collectSampleSelections() };
  if (!selectAll && !payload.selections.length) {
    showToast("请先选择需要采样的数据量");
    return;
  }
  const data = await api(`/api/tasks/${state.taskId}/sample`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  renderSampleResult(data.result);
  showToast("采样完成");
  await loadTasks();
  state.activeTask = taskById(state.taskId);
  await reloadSampleBuckets();
}

function renderSampleResult(result) {
  const buckets = result?.buckets || [];
  if (!$("sampleResult")) return;
  $("sampleResult").innerHTML = `
    <div class="bucketCard">
        <strong>${result?.sampled_count || 0}/${result?.candidate_count || 0}</strong>
        <span>采样/候选</span>
    </div>
    ${buckets.map((bucket) => `
      <div class="bucketCard">
        <strong>${bucket.sampled_count}/${bucket.candidate_count}</strong>
        <span>${escapeHtml(bucket.bucket)}</span>
      </div>
    `).join("")}
  `;
}

async function openVisualizationPage() {
  state.visualizationPage = 0;
  state.activeTask = taskById(state.taskId) || { id: state.taskId, name: state.taskId };
  await reloadVisualizationResults();
  renderVisualizationPage();
  refreshVisualizationFilterOptions().catch((error) => console.warn(error));
}

async function reloadVisualizationResults(options = {}) {
  const params = new URLSearchParams({
    page: String(state.visualizationPage),
    limit: "1",
  });
  params.set("include_filter_options", options.includeFilterOptions === true ? "1" : "0");
  if (hasActiveVisualizationFilters()) {
    params.set("filters", JSON.stringify(buildVisualizationFilterPayload()));
  }
  const data = await api(`/api/tasks/${state.taskId}/results?${params.toString()}`);
  state.visualizationResults = data.results || [];
  state.visualizationTotal = Number(data.total || 0);
  state.visualizationFilterOptions = data.filter_options || state.visualizationFilterOptions;
  if (state.visualizationPage >= state.visualizationTotal) {
    state.visualizationPage = Math.max(0, state.visualizationTotal - 1);
    if (state.visualizationTotal > 0) {
      await reloadVisualizationResults(options);
    }
  }
}

async function refreshVisualizationFilterOptions() {
  const data = await api(`/api/tasks/${state.taskId}/results/filter-options`);
  state.visualizationFilterOptions = data.filter_options || state.visualizationFilterOptions;
  renderVisualizationFilterPanel();
}

function renderVisualizationPage() {
  $("visualizationTitle").textContent = `${state.activeTask?.name || state.taskId} · 结果展示`;
  $("emptyVisualization").classList.toggle("hidden", state.visualizationTotal > 0);
  $("visualizationBody").classList.toggle("hidden", state.visualizationTotal === 0);
  if (!state.visualizationTotal || !state.visualizationResults.length) {
    $("visualizationProgress").textContent = "暂无数据";
    $("visualizationSrcImage").removeAttribute("src");
    $("visualizationDstImage").removeAttribute("src");
    setImagePath("visualizationSrcPath", "");
    setImagePath("visualizationDstPath", "");
    renderVisualizationImagePrompt(null);
    $("visualizationResultPanel").innerHTML = "";
    return;
  }

  const item = state.visualizationResults[0];
  $("visualizationProgress").textContent = `第 ${state.visualizationPage + 1} / ${state.visualizationTotal} 条`;
  $("visualizationJumpInput").value = state.visualizationPage + 1;
  $("visualizationJumpInput").max = state.visualizationTotal;
  setImagePath("visualizationSrcPath", item.src_relative_path || item.src_image || "");
  setImagePath("visualizationDstPath", item.dst_relative_path || item.dst_image || "");
  preparePreviewImage(
    $("visualizationSrcImage"),
    item.image_urls?.src || `/api/tasks/${state.taskId}/images/${item.item_index}/src`,
  );
  preparePreviewImage(
    $("visualizationDstImage"),
    item.image_urls?.dst || `/api/tasks/${state.taskId}/images/${item.item_index}/dst`,
  );
  $("visualizationSrcImage").onerror = () => $("visualizationSrcImage").removeAttribute("src");
  $("visualizationDstImage").onerror = () => $("visualizationDstImage").removeAttribute("src");
  renderVisualizationImagePrompt(item);
  renderUnifiedResultPanel(item);
}

function renderUnifiedResultPanel(item) {
  $("visualizationResultPanel").innerHTML = `
    <h2>结果</h2>
    <div class="badgeRow">
      ${statusBadge("粗筛", item.status?.rough_passed, item.status?.rough_completed)}
      ${statusBadge("精筛", item.status?.fine_passed, item.status?.fine_completed)}
      ${resultBadge(item.sampled ? "已采样" : "未采样", item.sampled ? "pass" : "")}
      ${resultBadge(item.label ? "已编辑标签" : "未编辑标签", item.label ? "pass" : "")}
    </div>
    ${renderScreeningRecord("粗筛聚合", item.rough)}
    ${renderScreeningRecord("精筛聚合", item.fine)}
    <section class="resultBlock">
      <h3>采样</h3>
      <div class="tagRows">
        ${resultRow("状态", item.sampled ? "已采样" : "未采样")}
        ${resultRow("采样桶", item.sample_bucket || "未分组")}
      </div>
    </section>
    ${renderEditableLabels(item)}
    ${renderLabelRevisionHistory(item)}
  `;
}

function statusBadge(label, passed, completed) {
  if (passed) return resultBadge(`${label}通过`, "pass");
  if (completed) return resultBadge(`${label}未通过`, "fail");
  return resultBadge(`${label}未完成`);
}

function renderScreeningRecord(title, record) {
  if (!record) {
    return `
      <section class="resultBlock">
        <h3>${escapeHtml(title)}</h3>
        <div class="metaText">暂无记录</div>
      </section>
    `;
  }
  const issues = Array.isArray(record.issues) ? record.issues.join("，") : "";
  return `
    <section class="resultBlock">
      <h3>${escapeHtml(title)}</h3>
      <div class="tagRows">
        ${resultRow("标注者", record.username)}
        ${resultRow("MOS", record.mos)}
        ${resultRow("瑕疵", record.has_defect ? "是" : "否")}
        ${resultRow("问题项", issues)}
        ${resultRow("备注", record.note)}
        ${resultRow("时间", formatTimestamp(record.updated_at))}
      </div>
    </section>
  `;
}

function renderLabelRows(title, labels) {
  const rows = flattenLabelRows(labels || {});
  return `
    <section class="resultBlock">
      <h3>${escapeHtml(title)}</h3>
      <div class="tagRows">
        ${rows.map((row) => resultRow(row.path, row.value)).join("") || '<div class="metaText">暂无标签</div>'}
      </div>
    </section>
  `;
}

function resultLabelPaths(item) {
  const selected = state.activeTask?.selected_label_paths || [];
  if (selected.length) return selected;
  return flattenLabelRows(item.effective_labels || item.original_labels || {}).map((row) => row.parts);
}

function renderEditableLabels(item) {
  const labels = item.effective_labels || item.original_labels || {};
  const paths = resultLabelPaths(item);
  return `
    <section class="resultBlock">
      <h3>标签</h3>
      <div class="tagRows editableLabelRows">
        ${paths.map((path) => renderEditableLabelRow(path, getNested(labels, path), item)).join("") || '<div class="metaText">暂无标签</div>'}
      </div>
    </section>
  `;
}

function renderEditableLabelRow(path, value, item) {
  const labelMeta = item.label || {};
  const meta = [
    labelMeta.username ? `编辑人 ${labelMeta.username}` : "",
    labelMeta.updated_at ? formatTimestamp(labelMeta.updated_at) : "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="tagRow resultEditableLabelRow" data-result-label-path="${escapeHtml(JSON.stringify(path))}">
      <div class="tagKey">${escapeHtml(path.join("/"))}</div>
      <button class="tagValue resultLabelValue" type="button">${escapeHtml(value ?? "未选择")}</button>
      <div class="tagMeta">${escapeHtml(meta)}</div>
    </div>
  `;
}

function currentVisualizationItem() {
  return state.visualizationResults[0] || null;
}

function findResultLabelDimension(path) {
  const [groupName, ...dimensionParts] = path;
  const dimensionName = dimensionParts.join("/");
  const group = (state.activeTask?.label_option_groups || []).find((entry) => entry.name === groupName);
  return (group?.dimensions || []).find((entry) => entry.name === dimensionName) || null;
}

function optionsWithCurrentValue(options, currentValue) {
  const values = [...(options || [])];
  if (currentValue !== undefined && currentValue !== null && currentValue !== "" && !values.some((value) => String(value) === String(currentValue))) {
    values.unshift(currentValue);
  }
  return values;
}

function renderResultSelectEditor(options, currentValue) {
  const select = document.createElement("select");
  select.className = "qcInlineEditor";
  if (currentValue === undefined || currentValue === null || currentValue === "") {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "未选择";
    empty.selected = true;
    select.appendChild(empty);
  }
  for (const optionValue of optionsWithCurrentValue(options, currentValue)) {
    const option = document.createElement("option");
    option.value = String(optionValue);
    option.textContent = String(optionValue);
    option.selected = String(optionValue) === String(currentValue);
    select.appendChild(option);
  }
  return select;
}

function beginVisualizationLabelEdit(row) {
  const item = currentVisualizationItem();
  if (!item) return;
  const path = JSON.parse(row.dataset.resultLabelPath || "[]");
  const button = row.querySelector(".resultLabelValue");
  if (!button) return;
  const currentValue = getNested(item.effective_labels || {}, path);
  const dimension = findResultLabelDimension(path);
  const editor = renderResultSelectEditor(dimension?.options || [], currentValue);
  button.replaceWith(editor);
  editor.focus();
  let saved = false;
  const save = () => {
    if (saved) return;
    saved = true;
    const nextLabels = mergeLabelObjects(item.effective_labels || item.original_labels || {}, {});
    setNested(nextLabels, path, editor.value);
    saveVisualizationLabels(item, nextLabels).catch((error) => showToast(error.message));
  };
  editor.addEventListener("change", save);
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") renderVisualizationPage();
  });
  editor.addEventListener("blur", save, { once: true });
}

async function saveVisualizationLabels(item, labels) {
  if (!state.username) {
    throw new Error("请先登录");
  }
  const data = await api(`/api/tasks/${state.taskId}/results/${item.item_index}/labels`, {
    method: "POST",
    body: JSON.stringify({ username: state.username, labels }),
  });
  item.label = {
    username: data.record.username,
    labels: data.record.labels,
    updated_at: data.record.updated_at,
  };
  item.effective_labels = data.record.labels;
  item.label_revisions = data.record.label_revisions || [];
  renderVisualizationPage();
  showToast("标签已保存");
}

function renderLabelRevisionHistory(item) {
  const revisions = item.label_revisions || [];
  return `
    <details class="resultBlock revisionHistory">
      <summary>编辑历史 ${revisions.length}</summary>
      <div class="revisionList">
        ${revisions.map(renderLabelRevision).join("") || '<div class="metaText">暂无编辑历史</div>'}
      </div>
    </details>
  `;
}

function renderLabelRevision(revision) {
  return `
    <article class="revisionItem">
      <div class="revisionHead">
        <strong>${escapeHtml(revision.username || "未知用户")}</strong>
        <span>${escapeHtml(formatTimestamp(revision.updated_at))}</span>
      </div>
      ${renderLabelRows("修改前", revision.before)}
      ${renderLabelRows("修改后", revision.after)}
    </article>
  `;
}

function flattenLabelRows(value, prefix = []) {
  if (!value || typeof value !== "object") return [];
  const rows = [];
  for (const [key, child] of Object.entries(value)) {
    const path = [...prefix, key];
    if (child && typeof child === "object" && !Array.isArray(child)) {
      rows.push(...flattenLabelRows(child, path));
    } else {
      rows.push({ path: path.join("/"), parts: path, value: Array.isArray(child) ? child.join("，") : child });
    }
  }
  return rows;
}

function preparePreviewImage(image, previewSrc) {
  image.loading = "eager";
  image.decoding = "async";
  image.fetchPriority = "high";
  if (image.src !== previewSrc) {
    image.src = previewSrc;
  }
  image.dataset.originalSrc = `${previewSrc}?original=1`;
}

function preloadStageNeighbors() {
  if (!state.items.length) return;
  preloadNeighborItems(state.items, state.index);
}

function preloadNeighborItems(items, currentIndex) {
  for (let offset = 1; offset <= PRELOAD_FORWARD_PAGES; offset += 1) {
    const item = items[currentIndex + offset];
    if (!item) continue;
    preloadImage(item.image_urls?.src || `/api/tasks/${state.activeTask.id}/images/${item.item_index}/src`);
    preloadImage(item.image_urls?.dst || `/api/tasks/${state.activeTask.id}/images/${item.item_index}/dst`);
  }
}

function preloadImage(src) {
  if (!src || preloadedImages.has(src)) return;
  const image = new Image();
  image.loading = "eager";
  image.decoding = "async";
  image.src = src;
  preloadedImages.set(src, image);
  if (preloadedImages.size > MAX_PRELOADED_IMAGES) {
    const oldestKey = preloadedImages.keys().next().value;
    preloadedImages.delete(oldestKey);
  }
}

function resultRow(label, value) {
  const displayValue = value === undefined || value === null || value === "" ? "无" : String(value);
  return `
    <div class="tagRow">
      <div class="tagKey">${escapeHtml(label)}</div>
      <div class="tagValue">${escapeHtml(displayValue)}</div>
    </div>
  `;
}

function resultBadge(label, kind = "") {
  return `<span class="resultBadge ${kind}">${escapeHtml(label)}</span>`;
}

function formatTimestamp(value) {
  if (!value) return "";
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

function setImagePath(targetId, value) {
  const target = $(targetId);
  if (!target) return;
  target.textContent = value || "";
  target.title = value || "";
}

function emptyVisualizationFilters() {
  return { statuses: [], mos: [], has_defect: [], annotators: [], labels: {} };
}

function visualizationFilterKey(path) {
  return JSON.stringify(path);
}

function normalizeVisualizationLabelFilter(entry) {
  return {
    values: Array.isArray(entry?.values) ? entry.values : [],
  };
}

function hasActiveVisualizationFilters() {
  const payload = buildVisualizationFilterPayload();
  return Boolean(
    payload.statuses.length ||
    payload.mos.length ||
    payload.has_defect.length ||
    payload.annotators.length ||
    payload.labels.some((filter) => filter.values.length)
  );
}

function buildVisualizationFilterPayload() {
  return {
    statuses: state.visualizationFilters.statuses || [],
    mos: state.visualizationFilters.mos || [],
    has_defect: state.visualizationFilters.has_defect || [],
    annotators: state.visualizationFilters.annotators || [],
    labels: Object.entries(state.visualizationFilters.labels || {}).map(([key, entry]) => {
      const normalized = normalizeVisualizationLabelFilter(entry);
      return {
        path: JSON.parse(key),
        values: normalized.values,
      };
    }),
  };
}

function renderVisualizationFilterPanel() {
  const body = $("visualizationFilterBody");
  if (!body) return;
  const options = state.visualizationFilterOptions || {};
  body.innerHTML = "";
  body.appendChild(renderVisualizationFilterGroup("状态", options.statuses || [], "statuses", state.visualizationFilters.statuses));
  body.appendChild(renderVisualizationFilterGroup("MOS 分", visualizationFilterOptionsOrDefault(options.mos, [1, 2, 3, 4, 5]), "mos", state.visualizationFilters.mos));
  body.appendChild(renderVisualizationFilterGroup(
    "是否有质量问题",
    visualizationFilterOptionsOrDefault(options.has_defect, [false, true]).map((value) => ({ value, label: value ? "有质量问题" : "无质量问题" })),
    "has_defect",
    state.visualizationFilters.has_defect,
  ));
  body.appendChild(renderVisualizationFilterGroup("标注者", options.annotators || [], "annotators", state.visualizationFilters.annotators, null, "暂无标注者"));

  const labelOptions = options.label_options || [];
  if (!labelOptions.length) {
    const empty = document.createElement("div");
    empty.className = "metaText";
    empty.textContent = "暂无 tag 维度";
    body.appendChild(empty);
    return;
  }
  for (const group of labelOptions) {
    const section = document.createElement("section");
    section.className = "filterSection";
    section.innerHTML = `<h3>${escapeHtml(group.name)}</h3>`;
    for (const dimension of group.dimensions || []) {
      const path = [group.name, ...String(dimension.name).split("/")];
      const key = visualizationFilterKey(path);
      const selected = normalizeVisualizationLabelFilter(state.visualizationFilters.labels[key]).values;
      section.appendChild(renderVisualizationFilterGroup(dimension.name, dimension.options || [], "label", selected, path));
    }
    body.appendChild(section);
  }
}

function visualizationFilterOptionsOrDefault(options, fallbackOptions) {
  return Array.isArray(options) && options.length ? options : fallbackOptions;
}

function renderVisualizationFilterGroup(title, options, type, selectedValues, path = null, emptyText = "暂无可选项") {
  const fieldset = document.createElement("fieldset");
  fieldset.className = "filterGroup";
  fieldset.innerHTML = `<legend>${escapeHtml(title)}</legend>`;
  const list = document.createElement("div");
  list.className = "labelOptions";
  if (!options.length) {
    list.innerHTML = `<span class="emptyFilterOption">${escapeHtml(emptyText)}</span>`;
  }
  for (const option of options) {
    const value = typeof option === "object" ? option.value : option;
    const labelText = typeof option === "object" ? option.label : option;
    const label = document.createElement("label");
    label.className = "labelOption";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.visualizationFilterType = type;
    input.value = String(value);
    if (path) input.dataset.labelPath = JSON.stringify(path);
    input.checked = (selectedValues || []).some((selected) => String(selected) === String(value));
    const span = document.createElement("span");
    span.textContent = String(labelText);
    label.appendChild(input);
    label.appendChild(span);
    list.appendChild(label);
  }
  fieldset.appendChild(list);
  return fieldset;
}

function collectVisualizationFilters() {
  const filters = emptyVisualizationFilters();
  $("visualizationFilterBody").querySelectorAll('input[type="checkbox"]:checked').forEach((input) => {
    const value = input.value;
    if (input.dataset.visualizationFilterType === "statuses") {
      filters.statuses.push(value);
      return;
    }
    if (input.dataset.visualizationFilterType === "mos") {
      filters.mos.push(Number(value));
      return;
    }
    if (input.dataset.visualizationFilterType === "has_defect") {
      filters.has_defect.push(value === "true");
      return;
    }
    if (input.dataset.visualizationFilterType === "annotators") {
      filters.annotators.push(value);
      return;
    }
    if (input.dataset.visualizationFilterType === "label") {
      const path = JSON.parse(input.dataset.labelPath || "[]");
      const key = visualizationFilterKey(path);
      filters.labels[key] = normalizeVisualizationLabelFilter(filters.labels[key]);
      filters.labels[key].values.push(value);
    }
  });
  return filters;
}

function openVisualizationFilterPanel() {
  renderVisualizationFilterPanel();
  $("visualizationFilterOverlay").classList.remove("hidden");
  $("visualizationFilterPanel").classList.remove("hidden");
  $("visualizationFilterPanel").setAttribute("aria-hidden", "false");
}

function closeVisualizationFilterPanel() {
  $("visualizationFilterOverlay").classList.add("hidden");
  $("visualizationFilterPanel").classList.add("hidden");
  $("visualizationFilterPanel").setAttribute("aria-hidden", "true");
}

async function applyVisualizationFilter() {
  state.visualizationFilters = collectVisualizationFilters();
  state.visualizationPage = 0;
  await reloadVisualizationResults();
  renderVisualizationPage();
  closeVisualizationFilterPanel();
}

async function clearVisualizationFilter() {
  state.visualizationFilters = emptyVisualizationFilters();
  state.visualizationPage = 0;
  await reloadVisualizationResults();
  renderVisualizationPage();
  renderVisualizationFilterPanel();
}

async function goToVisualizationPage(nextPage) {
  const boundedPage = Math.max(0, Math.min(Math.max(0, state.visualizationTotal - 1), nextPage));
  if (boundedPage === state.visualizationPage && state.visualizationResults.length) return;
  state.visualizationPage = boundedPage;
  await reloadVisualizationResults();
  renderVisualizationPage();
}

function bindEvents() {
  $("loginForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    state.username = $("loginUsernameInput").value.trim();
    localStorage.setItem("annotations_v2.username", state.username);
    enterApp().catch((error) => showToast(error.message));
  });
  $("logoutBtn")?.addEventListener("click", () => {
    state.username = "";
    localStorage.removeItem("annotations_v2.username");
    showLogin();
  });
  $("refreshBtn")?.addEventListener("click", () => loadTasks().catch((error) => showToast(error.message)));
  $("createTaskForm")?.addEventListener("submit", (event) => createTask(event).catch((error) => showToast(error.message)));
  $("taskList")?.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action][data-id]");
    const action = target?.dataset.action;
    const taskId = target?.dataset.id;
    if (!action || !taskId) return;
    if (action === "edit") openEditTaskDialog(taskId);
    if (action === "import") importTaskAnnotations(taskId).catch((error) => showToast(error.message));
    if (action === "cache-previews") warmPreviewCache(taskId, target).catch((error) => showToast(error.message));
    if (action === "delete") deleteTask(taskId, target.dataset.name || "").catch((error) => showToast(error.message));
  });
  $("editTaskForm")?.addEventListener("submit", (event) => saveTaskEdits(event).catch((error) => showToast(error.message)));
  $("taskEditOverlay")?.addEventListener("click", () => closeEditTaskDialog());
  $("closeEditTaskBtn")?.addEventListener("click", () => closeEditTaskDialog());
  $("cancelEditTaskBtn")?.addEventListener("click", () => closeEditTaskDialog());
  $("stageForm")?.addEventListener("submit", (event) => saveStage(event).catch((error) => showToast(error.message)));
  $("prevBtn")?.addEventListener("click", () => goToItem(state.index - 1).catch((error) => showToast(error.message)));
  $("nextBtn")?.addEventListener("click", () => {
    goToItem(state.index + 1).catch((error) => showToast(error.message));
  });
  $("runSampleBtn")?.addEventListener("click", () => runSample(false).catch((error) => showToast(error.message)));
  $("selectAllSampleBtn")?.addEventListener("click", () => runSample(true).catch((error) => showToast(error.message)));
  $("sampleBucketList")?.addEventListener("click", (event) => {
    const target = event.target.closest("[data-sample-all-bucket]");
    if (!target) return;
    const input = document.querySelector(`.sampleCountInput[data-bucket-index="${target.dataset.sampleAllBucket}"]`);
    const bucket = state.sampleBuckets[Number(target.dataset.sampleAllBucket)];
    if (input && bucket) input.value = bucket.candidate_count || 0;
  });
  $("visualizationPrevBtn")?.addEventListener("click", () => {
    goToVisualizationPage(state.visualizationPage - 1).catch((error) => showToast(error.message));
  });
  $("visualizationNextBtn")?.addEventListener("click", () => {
    goToVisualizationPage(state.visualizationPage + 1).catch((error) => showToast(error.message));
  });
  $("visualizationJumpBtn")?.addEventListener("click", () => {
    const target = Number($("visualizationJumpInput").value || 1) - 1;
    goToVisualizationPage(target).catch((error) => showToast(error.message));
  });
  $("visualizationResultPanel")?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-result-label-path]");
    if (!row || event.target.closest(".qcInlineEditor")) return;
    beginVisualizationLabelEdit(row);
  });
  $("openVisualizationFilterBtn")?.addEventListener("click", () => openVisualizationFilterPanel());
  $("visualizationFilterOverlay")?.addEventListener("click", () => closeVisualizationFilterPanel());
  $("closeVisualizationFilterBtn")?.addEventListener("click", () => closeVisualizationFilterPanel());
  $("applyVisualizationFilterBtn")?.addEventListener("click", () => {
    applyVisualizationFilter().catch((error) => showToast(error.message));
  });
  $("clearVisualizationFilterBtn")?.addEventListener("click", () => {
    clearVisualizationFilter().catch((error) => showToast(error.message));
  });
  document.addEventListener("keydown", handleRateShortcuts);
}

bindEvents();
enterApp().catch((error) => showToast(error.message));
