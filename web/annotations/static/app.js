const state = {
  username: localStorage.getItem("annotations.username") || "",
  threshold: Number(localStorage.getItem("annotations.threshold") || 4),
  nextKey: localStorage.getItem("annotations.nextKey") || "R",
  tasks: [],
  taskId: null,
  taskName: "",
  subtask: null,
  page: 0,
  currentMos: null,
  currentTags: {},
  results: [],
  resultPage: 0,
  visualizationResults: [],
  visualizationPage: 0,
  visualizationTotal: 0,
  resultFilterOptions: { mos: [], annotators: [], label_options: [] },
  resultFilters: { mos: [], annotators: [], labels: {} },
  statistics: null,
  statsFilters: { mos: [], annotators: [], labels: {} },
  statsCombinations: [],
  statsChartType: ["bar", "pie"].includes(localStorage.getItem("annotations.statsChartType")) ? localStorage.getItem("annotations.statsChartType") : "bar",
  activeFilterTarget: "results",
  issues: [],
  activeIssueId: null,
  issueReturnItemIndex: null,
  issueSelection: null,
  pairLabelOptions: { name: "Pair", dimensions: [{ name: "问题标签", options: [] }, { name: "优势标签", options: [] }] },
};

const preloadedImages = new Map();
const MAX_PRELOADED_IMAGES = 80;

const $ = (id) => document.getElementById(id);

function show(viewId) {
  ["loginView", "homeView", "annotateView", "resultsView", "issuesView", "visualizationView", "statsView"].forEach((id) => {
    $(id).classList.toggle("hidden", id !== viewId);
  });
  document.body.dataset.view = viewId;
  syncTopbarWork(viewId);
}

function syncTopbarWork(viewId) {
  const slot = $("topbarWorkSlot");
  restoreBottomPager(viewId);
  const activeHeader = document.querySelector(`#${viewId} > .workHeader`);
  const dockedHeader = slot.querySelector(".workHeader");
  if (!activeHeader && dockedHeader?.dataset.ownerView === viewId) {
    slot.classList.remove("hidden");
    dockBottomPager(viewId, dockedHeader);
    return;
  }
  if (dockedHeader && dockedHeader !== activeHeader) {
    const owner = $(dockedHeader.dataset.ownerView);
    if (owner) owner.prepend(dockedHeader);
    dockedHeader.classList.remove("inTopbar");
  }
  slot.classList.toggle("hidden", !activeHeader);
  if (!activeHeader) return;
  activeHeader.dataset.ownerView = viewId;
  activeHeader.classList.add("inTopbar");
  slot.appendChild(activeHeader);
  dockBottomPager(viewId, activeHeader);
}

function dockBottomPager(viewId, header) {
  const target = {
    resultsView: $("resultsPagerSlot"),
    visualizationView: $("visualizationPagerSlot"),
  }[viewId];
  const pager = header.querySelector(".pager");
  if (!target || !pager) return;
  pager.dataset.ownerView = viewId;
  pager.classList.add("inBottomPager");
  target.appendChild(pager);
}

function restoreBottomPager(nextViewId) {
  document.querySelectorAll(".bottomPager > .pager").forEach((pager) => {
    if (pager.dataset.ownerView === nextViewId) return;
    const ownerView = pager.dataset.ownerView;
    const ownerHeader = Array.from(document.querySelectorAll(".workHeader"))
      .find((header) => header.dataset.ownerView === ownerView);
    if (ownerHeader) ownerHeader.appendChild(pager);
    pager.classList.remove("inBottomPager");
  });
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  setTimeout(() => node.classList.add("hidden"), 2800);
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

function updateSession() {
  $("sessionLine").textContent = state.username ? `当前用户：${state.username}` : "未登录";
  $("thresholdInput").value = state.threshold;
  $("nextKeyInput").value = state.nextKey;
}

function requireLogin() {
  updateSession();
  if (!state.username) {
    show("loginView");
    return false;
  }
  show("homeView");
  return true;
}

async function loadTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.tasks;
  renderTasks();
}

function renderTasks() {
  const list = $("taskList");
  list.innerHTML = "";
  if (!state.tasks.length) {
    list.innerHTML = '<div class="taskCard taskMeta">暂无任务</div>';
    return;
  }
  for (const task of state.tasks) {
    const card = document.createElement("div");
    card.className = "taskCard";
    card.innerHTML = `
      <h3>${escapeHtml(task.name)}</h3>
      <div class="taskMeta">
        数据 ${task.item_count} 条 · 子任务 ${task.subtask_count} 个 ·
        已分配 ${task.assigned_count} · 已完成 ${task.completed_count} · 已标注 ${task.annotation_count}
      </div>
      <div class="taskMeta">${escapeHtml(task.jsonl_path)}</div>
      <div class="taskActions">
        <button data-action="annotate" data-id="${task.id}" data-name="${escapeAttr(task.name)}">进入标注</button>
        <button class="ghost" data-action="results" data-id="${task.id}" data-name="${escapeAttr(task.name)}">结果展示</button>
        <button class="ghost" data-action="issues" data-id="${task.id}" data-name="${escapeAttr(task.name)}">Issues</button>
        <button class="ghost" data-action="stats" data-id="${task.id}" data-name="${escapeAttr(task.name)}">结果统计</button>
        <button class="ghost" data-action="download-jsonl" data-id="${task.id}">下载 JSONL</button>
        <button class="ghost" data-action="download-xlsx" data-id="${task.id}">下载 Excel</button>
        <button class="ghost" data-action="refresh-labels" data-id="${task.id}">更新AI标签</button>
        <button class="ghost" data-action="visualization" data-id="${task.id}" data-name="${escapeAttr(task.name)}">全部数据可视化</button>
        <button class="ghost" data-action="cache-inputs" data-id="${task.id}">缓存输入图</button>
        ${state.username === "孙本猿" ? `<button class="dangerBtn" data-action="delete" data-id="${task.id}" data-name="${escapeAttr(task.name)}">删除任务</button>` : ""}
      </div>
    `;
    list.appendChild(card);
  }
}

async function deleteTask(taskId, taskName) {
  if (!window.confirm(`确认删除任务“${taskName}”？该操作会删除任务分配和标注结果。`)) {
    return;
  }
  await api(`/api/tasks/${taskId}`, { method: "DELETE", body: JSON.stringify({ username: state.username }) });
  toast("任务已删除");
  await loadTasks();
}

async function refreshTaskLabels(taskId, button) {
  if (button) button.disabled = true;
  try {
    const data = await api(`/api/tasks/${taskId}/labels/refresh`, { method: "POST" });
    const result = data.result || {};
    toast(`AI标签已更新：${result.updated_count || 0} 条变化，${result.labeled_count || 0} 条有标签`);
    await loadTasks();
  } finally {
    if (button) button.disabled = false;
  }
}

async function warmInputPreviewCache(taskId, button) {
  const originalText = button?.textContent || "缓存输入图";
  if (button) {
    button.disabled = true;
    button.textContent = "缓存中 0%";
  }
  try {
    const data = await api(`/api/tasks/${taskId}/preview-cache/jobs`, { method: "POST" });
    const job = await waitForPreviewCacheJob(taskId, data.job.id, button);
    const result = job.result || {};
    toast(`输入图缓存完成：生成 ${result.generated_count || 0}，跳过 ${result.skipped_count || 0}，失败 ${result.failed_count || 0}`);
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
    if (job.status === "failed") throw new Error(job.error || "输入图缓存失败");
    await sleep(350);
  }
}

async function createTask(event) {
  event.preventDefault();
  const submitButton = $("createTaskForm").querySelector('button[type="submit"]');
  const payload = {
    name: $("taskNameInput").value.trim(),
    root_dir: $("rootDirInput").value.trim(),
    annotation_dir: $("annotationDirInput").value.trim(),
    jsonl_path: $("jsonlPathInput").value.trim(),
    chunk_size: Number($("chunkSizeInput").value || 100),
    shuffle_items: $("shuffleItemsInput").checked,
  };
  try {
    submitButton.disabled = true;
    setCreateProgress(0, "正在提交创建任务");
    const data = await api("/api/tasks/jobs", { method: "POST", body: JSON.stringify(payload) });
    await waitForCreateJob(data.job.id);
    toast("任务已创建");
    $("createTaskForm").reset();
    $("chunkSizeInput").value = 100;
    $("createProgress").classList.add("hidden");
    await loadTasks();
  } finally {
    submitButton.disabled = false;
  }
}

async function waitForCreateJob(jobId) {
  while (true) {
    const data = await api(`/api/tasks/jobs/${jobId}`);
    const job = data.job;
    setCreateProgress(job.progress || 0, job.message || "正在创建任务");
    if (job.status === "completed") return job.task;
    if (job.status === "failed") throw new Error(job.error || "任务创建失败");
    await sleep(350);
  }
}

function setCreateProgress(progress, message) {
  const normalized = Math.max(0, Math.min(100, Number(progress || 0)));
  $("createProgress").classList.remove("hidden");
  $("createProgressText").textContent = message;
  $("createProgressPercent").textContent = `${normalized}%`;
  $("createProgressBar").value = normalized;
}

async function startAnnotation(taskId, taskName) {
  const data = await api(`/api/tasks/${taskId}/assign`, {
    method: "POST",
    body: JSON.stringify({ username: state.username }),
  });
  if (!data.subtask) {
    toast(data.message || "没有可分配的子任务");
    return;
  }
  state.taskId = taskId;
  state.taskName = taskName;
  state.subtask = data.subtask;
  state.pairLabelOptions = data.subtask.pair_label_options || state.pairLabelOptions;
  const firstUnfinished = data.subtask.items.findIndex((item) => !item.annotation);
  state.page = firstUnfinished >= 0 ? firstUnfinished : 0;
  show("annotateView");
  renderAnnotationPage();
}

function renderScoreBar() {
  const bar = $("scoreBar");
  bar.innerHTML = "";
  for (let score = 1; score <= 5; score += 1) {
    const button = document.createElement("button");
    button.className = `scoreBtn${state.currentMos === score ? " active" : ""}`;
    button.textContent = `${score} 分`;
    button.addEventListener("click", () => chooseMos(score));
    bar.appendChild(button);
  }
}

function chooseMos(score) {
  state.currentMos = score;
  renderScoreBar();
  if (score < state.threshold) {
    setTimeout(() => saveAndMove(1), 120);
  }
}

function renderAnnotationPage() {
  const item = currentItem();
  if (!item) return;
  const total = state.subtask.items.length;
  const annotation = item.annotation || {};
  state.currentMos = annotation.mos || null;
  state.currentTags = deepClone(annotation.tags || item.labels || item.tags || {});

  $("workTitle").textContent = `${state.taskName} / 子任务 ${state.subtask.index}`;
  $("progressText").textContent = `第 ${state.page + 1} / ${total} 条`;
  $("jumpInput").value = state.page + 1;
  $("jumpInput").max = total;
  preparePreviewImage($("srcImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/src`);
  preparePreviewImage($("dstImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/dst`);
  preloadAnnotationNeighbors();
  renderTagEditor();
  renderScoreBar();
}

function legacyTagEditor() {
  const groups = state.subtask?.label_options || [];
  const list = $("tagList");
  list.innerHTML = "";
  if (!groups.length) {
    list.innerHTML = '<div class="taskMeta">暂无标签，可在下方添加</div>';
    return;
  }
}

function renderTagEditor() {
  const groups = state.subtask?.label_options || [];
  const list = $("tagList");
  list.innerHTML = "";
  if (!groups.length) {
    list.innerHTML = '<div class="taskMeta">当前任务没有可展示的图像标签维度</div>';
  }
  for (const group of groups) {
    if (group.name === state.pairLabelOptions.name) continue;
    const groupNode = document.createElement("section");
    groupNode.className = "labelGroup";
    groupNode.innerHTML = `<h4>${escapeHtml(group.name)}</h4>`;

    for (const dimension of group.dimensions || []) {
      const path = [group.name, dimension.name];
      const currentValue = getNested(state.currentTags, path);
      const options = optionsWithCurrentValue(dimension.options || [], currentValue);
      const dimensionNode = document.createElement("div");
      dimensionNode.className = "labelDimension";
      dimensionNode.setAttribute("role", "group");
      dimensionNode.setAttribute("aria-label", dimension.name);
      dimensionNode.innerHTML = `<div class="labelDimensionName">${escapeHtml(dimension.name)}</div>`;
      if (dimension.name === "拍摄角度") {
        const input = document.createElement("input");
        input.className = "angleInput labelNumberInput";
        input.type = "number";
        input.step = "1";
        input.inputMode = "numeric";
        input.dataset.labelPath = JSON.stringify(path);
        input.value = Number.isInteger(Number(currentValue)) ? String(Number(currentValue)) : "";
        dimensionNode.appendChild(input);
        groupNode.appendChild(dimensionNode);
        continue;
      }

      const optionsNode = document.createElement("div");
      optionsNode.className = "labelOptions";
      for (const option of options) {
        const id = `label-${hashText(`${path.join(".")}.${String(option)}`)}`;
        const label = document.createElement("label");
        label.className = "labelOption";
        label.setAttribute("for", id);
        label.innerHTML = `
          <input id="${id}" class="labelOptionInput" type="radio" name="${escapeAttr(path.join("."))}" data-label-path="${escapeAttr(JSON.stringify(path))}" data-label-value="${escapeAttr(JSON.stringify(option))}"${isSelectedValue(option, currentValue) ? " checked" : ""}>
          <span>${escapeHtml(String(option))}</span>
        `;
        optionsNode.appendChild(label);
      }
      dimensionNode.appendChild(optionsNode);
      groupNode.appendChild(dimensionNode);
    }
    list.appendChild(groupNode);
  }
  list.appendChild(renderPairLabelGroup());
}

function renderPairLabelGroup() {
  const groupNode = document.createElement("section");
  groupNode.className = "labelGroup pairLabelGroup";
  groupNode.innerHTML = `<h4>${escapeHtml(state.pairLabelOptions.name)}</h4>`;
  for (const dimension of state.pairLabelOptions.dimensions || []) {
    const path = [state.pairLabelOptions.name, dimension.name];
    const currentValue = getNested(state.currentTags, path);
    const options = optionsWithCurrentValue(dimension.options || [], currentValue);
    const dimensionNode = document.createElement("div");
    dimensionNode.className = "labelDimension pairLabelDimension";
    dimensionNode.setAttribute("role", "group");
    dimensionNode.setAttribute("aria-label", dimension.name);
    dimensionNode.innerHTML = `<div class="labelDimensionName">${escapeHtml(dimension.name)}</div>`;

    const controls = document.createElement("div");
    controls.className = "pairLabelControls";
    const optionsNode = document.createElement("div");
    optionsNode.className = "labelOptions";
    if (!options.length) {
      optionsNode.innerHTML = '<div class="taskMeta pairEmptyOptions">暂无可选项</div>';
    }
    for (const option of options) {
      const id = `pair-label-${hashText(`${path.join(".")}.${String(option)}`)}`;
      const label = document.createElement("label");
      label.className = "labelOption";
      label.setAttribute("for", id);
      label.innerHTML = `
        <input id="${id}" class="labelOptionInput" type="radio" name="${escapeAttr(path.join("."))}" data-label-path="${escapeAttr(JSON.stringify(path))}" data-label-value="${escapeAttr(JSON.stringify(option))}"${isSelectedValue(option, currentValue) ? " checked" : ""}>
        <span>${escapeHtml(String(option))}</span>
      `;
      optionsNode.appendChild(label);
    }
    controls.appendChild(optionsNode);
    controls.insertAdjacentHTML("beforeend", `
      <div class="pairAddRow">
        <input class="pairAddInput" data-pair-dimension="${escapeAttr(dimension.name)}" placeholder="新增${escapeAttr(dimension.name)}">
        <button class="ghost pairAddBtn" type="button" data-pair-dimension="${escapeAttr(dimension.name)}">添加</button>
      </div>
    `);
    dimensionNode.appendChild(controls);
    groupNode.appendChild(dimensionNode);
  }
  return groupNode;
}

async function addPairLabelOption(dimensionName, label) {
  const trimmed = String(label || "").trim();
  if (!trimmed) {
    toast("请输入标签名称");
    return;
  }
  state.currentTags = collectTags();
  if (!window.confirm(`确认添加“${trimmed}”到${dimensionName}？添加后所有任务都可以使用。`)) {
    return;
  }
  const data = await api("/api/pair-label-options", {
    method: "POST",
    body: JSON.stringify({ dimension: dimensionName, label: trimmed }),
  });
  state.pairLabelOptions = data.pair_label_options || state.pairLabelOptions;
  setNested(state.currentTags, [state.pairLabelOptions.name, dimensionName], trimmed);
  renderTagEditor();
  toast("标签已添加");
}

function collectTags() {
  const result = {};
  for (const input of document.querySelectorAll(".labelOptionInput:checked")) {
    const path = JSON.parse(input.getAttribute("data-label-path"));
    setNested(result, path, JSON.parse(input.getAttribute("data-label-value")));
  }
  for (const input of document.querySelectorAll(".labelNumberInput")) {
    if (input.value.trim() === "") continue;
    setNested(result, JSON.parse(input.getAttribute("data-label-path")), parseInt(input.value, 10));
  }
  return result;
}

async function saveCurrent() {
  const item = currentItem();
  if (!item) return;
  if (!state.currentMos) {
    throw new Error("请先选择 MOS 分");
  }
  const data = await api(`/api/tasks/${state.taskId}/subtasks/${state.subtask.id}/annotations`, {
    method: "POST",
    body: JSON.stringify({
      username: state.username,
      item_index: item.item_index,
      mos: state.currentMos,
      tags: collectTags(),
    }),
  });
  item.annotation = data.annotation;
}

async function saveAndMove(delta) {
  try {
    await saveCurrent();
    await movePage(delta);
  } catch (error) {
    toast(error.message);
  }
}

async function movePage(delta) {
  const next = state.page + delta;
  if (next >= 0 && next < state.subtask.items.length) {
    state.page = next;
    renderAnnotationPage();
    return;
  }
  if (next >= state.subtask.items.length) {
    toast("当前子任务已完成，正在分配下一子任务");
    await startAnnotation(state.taskId, state.taskName);
  }
}

async function abandonSubtask() {
  if (!state.taskId || !state.subtask) return;
  if (!window.confirm("确认放弃当前子任务？该子任务内已保存的标注会被撤销并删除。")) {
    return;
  }
  const button = $("abandonSubtaskBtn");
  if (button) button.disabled = true;
  try {
    const data = await api(`/api/tasks/${state.taskId}/subtasks/${state.subtask.id}`, {
      method: "DELETE",
      body: JSON.stringify({ username: state.username }),
    });
    state.subtask = null;
    state.page = 0;
    toast(`已放弃子任务，删除 ${data.deleted_count || 0} 条标注`);
    show("homeView");
    await loadTasks();
  } finally {
    if (button) button.disabled = false;
  }
}

function currentItem() {
  return state.subtask?.items[state.page];
}

async function openResults(taskId, taskName) {
  state.taskId = taskId;
  state.taskName = taskName;
  state.resultPage = 0;
  state.resultFilters = emptyFilters();
  await reloadResults();
  show("resultsView");
  renderResultPage();
}

async function reloadResults() {
  const params = new URLSearchParams({ threshold: "1" });
  const filters = buildFilterPayload(state.resultFilters);
  if (hasActiveResultFilters(filters)) {
    params.set("filters", JSON.stringify(filters));
  }
  const data = await api(`/api/tasks/${state.taskId}/results?${params.toString()}`);
  state.results = data.results;
  state.resultFilterOptions = data.filter_options || { mos: [], annotators: [], label_options: [] };
  updatePairLabelOptionsFromFilterOptions(state.resultFilterOptions);
  if (state.resultPage >= state.results.length) {
    state.resultPage = Math.max(0, state.results.length - 1);
  }
}

async function openVisualization(taskId, taskName) {
  state.taskId = taskId;
  state.taskName = taskName;
  state.visualizationPage = 0;
  await reloadVisualizationResults();
  show("visualizationView");
  renderVisualizationPage();
}

async function reloadVisualizationResults() {
  const params = new URLSearchParams({
    page: String(state.visualizationPage),
    limit: "1",
  });
  const data = await api(`/api/tasks/${state.taskId}/visualization-results?${params.toString()}`);
  state.visualizationResults = data.results || [];
  state.visualizationTotal = Number(data.total || 0);
  if (state.visualizationPage >= state.visualizationTotal) {
    state.visualizationPage = Math.max(0, state.visualizationTotal - 1);
    if (state.visualizationTotal > 0) {
      await reloadVisualizationResults();
    }
  }
}

async function openStatistics(taskId, taskName) {
  state.taskId = taskId;
  state.taskName = taskName;
  state.statsFilters = emptyFilters();
  state.statsCombinations = [];
  await reloadStatistics();
  show("statsView");
  renderStatisticsPage();
}

async function reloadStatistics() {
  const params = new URLSearchParams();
  const filters = buildFilterPayload(state.statsFilters);
  if (hasActiveResultFilters(filters)) {
    params.set("filters", JSON.stringify(filters));
  }
  if (state.statsCombinations.length) {
    params.set("combinations", JSON.stringify(state.statsCombinations));
  }
  const query = params.toString();
  const data = await api(`/api/tasks/${state.taskId}/statistics${query ? `?${query}` : ""}`);
  state.statistics = data.statistics;
  if (state.statistics?.filter_options) {
    state.resultFilterOptions = state.statistics.filter_options;
    updatePairLabelOptionsFromFilterOptions(state.resultFilterOptions);
  }
}

async function openIssues(taskId, taskName) {
  state.taskId = taskId;
  state.taskName = taskName;
  await reloadIssues();
  show("issuesView");
  renderIssuesPage();
}

async function reloadIssues() {
  const data = await api(`/api/tasks/${state.taskId}/issues`);
  state.issues = data.issues || [];
  if (!state.issues.some((issue) => issue.id === state.activeIssueId)) {
    state.activeIssueId = state.issues[0]?.id || null;
  }
}

function renderIssuesPage() {
  $("issuesTitle").textContent = `${state.taskName} / Issues`;
  const openCount = state.issues.filter((issue) => issue.status === "open").length;
  $("issuesSummary").textContent = `${openCount} open / ${state.issues.length} total`;
  renderIssuesList();
  renderIssueDetail();
}

function renderIssuesList() {
  const list = $("issuesList");
  if (!state.issues.length) {
    list.innerHTML = '<div class="taskMeta">暂无 issue</div>';
    return;
  }
  list.innerHTML = state.issues.map((issue) => `
    <button class="issueListItem${issue.id === state.activeIssueId ? " active" : ""}" type="button" data-issue-id="${escapeAttr(issue.id)}">
      <span class="issueStatus ${escapeAttr(issue.status)}">${escapeHtml(issue.status)}</span>
      <strong>${escapeHtml(issue.title || "Untitled issue")}</strong>
      <span>提出人 ${escapeHtml(issue.created_by || "")} · 解决人 ${escapeHtml(issue.assigned_to || "")}</span>
      <span>样本 #${issue.item_index} · ${issue.answers?.length || 0} answers</span>
    </button>
  `).join("");
}

function renderIssueDetail() {
  const detail = $("issueDetail");
  const issue = state.issues.find((item) => item.id === state.activeIssueId);
  if (!issue) {
    detail.innerHTML = '<div class="taskMeta">请选择一个 issue</div>';
    return;
  }
  const snapshot = issue.snapshot || {};
  detail.innerHTML = `
    <div class="issueDetailHeader">
      <div>
        <h2>${escapeHtml(issue.title || "Untitled issue")}</h2>
        <div class="taskMeta">提出人 ${escapeHtml(issue.created_by || "")} · 解决人 ${escapeHtml(issue.assigned_to || "")} · 样本 #${issue.item_index}</div>
      </div>
      <div class="issueHeaderActions">
        <button class="ghost" type="button" data-issue-action="open-result">查看结果页</button>
        <button class="${issue.status === "open" ? "dangerBtn" : "ghost"}" type="button" data-issue-action="${issue.status === "open" ? "close" : "reopen"}">${issue.status === "open" ? "关闭" : "重开"}</button>
      </div>
    </div>
    <section class="issueQuestion">
      <h3>问题</h3>
      <p>${escapeHtml(issue.body || "请检查该条标注结果")}</p>
    </section>
    <div class="issueResultGrid">
      ${renderIssueImage("src", "原图", issue.item_index, snapshot.src_relative_path || snapshot.src_image)}
      ${renderIssueImage("dst", "生成图", issue.item_index, snapshot.dst_relative_path || snapshot.dst_image)}
      <aside class="issueSnapshot">
        <span class="resultBadge">MOS ${escapeHtml(snapshot.mos == null ? "未打分" : String(snapshot.mos))}</span>
        <span class="resultBadge">Annotator ${escapeHtml(snapshot.username || "")}</span>
        <div id="issueSnapshotTags"></div>
      </aside>
    </div>
    <section class="issueAnswers">
      <h3>回答</h3>
      <div class="issueAnswerList">
        ${(issue.answers || []).map((answer) => `
          <article class="issueAnswer">
            <strong>${escapeHtml(answer.author || "")}</strong>
            <p>${escapeHtml(answer.body || "")}</p>
          </article>
        `).join("") || '<div class="taskMeta">暂无回答</div>'}
      </div>
      <textarea id="issueAnswerInput" rows="5" placeholder="输入回答，或框选图片区域插入 bbox 引用"></textarea>
      <div class="issueAnswerActions">
        <button class="ghost" type="button" data-issue-action="select-src">框选原图</button>
        <button class="ghost" type="button" data-issue-action="select-dst">框选生成图</button>
        <button type="button" data-issue-action="answer">提交回答</button>
      </div>
    </section>
  `;
  renderReadonlyTags("issueSnapshotTags", snapshot.tags || {});
  preparePreviewImage($("issueSrcImage"), `/api/tasks/${state.taskId}/images/${issue.item_index}/src`);
  preparePreviewImage($("issueDstImage"), `/api/tasks/${state.taskId}/images/${issue.item_index}/dst`);
}

function renderIssueImage(kind, title, itemIndex, imagePath) {
  const id = kind === "src" ? "issueSrcImage" : "issueDstImage";
  return `
    <figure class="issueImagePanel">
      <figcaption>${escapeHtml(title)}</figcaption>
      <div class="imagePath">${escapeHtml(imagePath || "")}</div>
      <div class="issueImageShell" data-image-kind="${escapeAttr(kind)}">
        <img id="${id}" class="issueSelectableImage" data-image-kind="${escapeAttr(kind)}" alt="${escapeAttr(title)} #${itemIndex}" loading="lazy">
      </div>
    </figure>
  `;
}

async function submitIssueAnswer() {
  const issue = state.issues.find((item) => item.id === state.activeIssueId);
  if (!issue) return;
  const body = $("issueAnswerInput").value.trim();
  if (!body) {
    toast("请输入回答内容");
    return;
  }
  const data = await api(`/api/tasks/${state.taskId}/issues/${issue.id}/answers`, {
    method: "POST",
    body: JSON.stringify({ author: state.username, body }),
  });
  replaceIssue(data.issue);
  renderIssuesPage();
}

async function setIssueStatus(action) {
  const issue = state.issues.find((item) => item.id === state.activeIssueId);
  if (!issue) return;
  const data = await api(`/api/tasks/${state.taskId}/issues/${issue.id}/${action}`, {
    method: "POST",
    body: JSON.stringify({ username: state.username }),
  });
  replaceIssue(data.issue);
  renderIssuesPage();
}

function replaceIssue(issue) {
  state.issues = state.issues.map((item) => item.id === issue.id ? issue : item);
  state.activeIssueId = issue.id;
}

async function openIssueResult(issue) {
  if (!issue) return;
  await openResultsAtItem(state.taskId, state.taskName, issue.item_index);
}

async function openResultsAtItem(taskId, taskName, itemIndex) {
  state.taskId = taskId;
  state.taskName = taskName;
  state.resultPage = 0;
  state.resultFilters = emptyFilters();
  await reloadResults();
  const targetPage = state.results.findIndex((item) => Number(item.item_index) === Number(itemIndex));
  if (targetPage >= 0) {
    state.resultPage = targetPage;
  } else {
    toast("结果页中没有找到该样本");
  }
  show("resultsView");
  renderResultPage();
}

function exportIssuesMarkdown() {
  if (!state.taskId) return;
  window.location.href = `/api/tasks/${state.taskId}/issues/export.md`;
}

function openIssueModal() {
  const item = currentResultItem();
  if (!item) return;
  $("issueTitleInput").value = "请检查该条标注结果";
  $("issueBodyInput").value = "";
  $("issueAssigneeHint").textContent = `将自动分配给 ${item.username || "未知标注人"}`;
  $("issueModal").classList.remove("hidden");
  $("issueModal").setAttribute("aria-hidden", "false");
  $("issueBodyInput").focus();
}

function closeIssueModal() {
  $("issueModal").classList.add("hidden");
  $("issueModal").setAttribute("aria-hidden", "true");
}

async function submitResultIssue(event) {
  event.preventDefault();
  const item = currentResultItem();
  if (!item) return;
  const data = await api(`/api/tasks/${state.taskId}/issues`, {
    method: "POST",
    body: JSON.stringify({
      item_index: item.item_index,
      created_by: state.username,
      title: $("issueTitleInput").value,
      body: $("issueBodyInput").value,
    }),
  });
  closeIssueModal();
  toast(`Issue 已分配给 ${data.issue.assigned_to || "标注人"}`);
}

function beginIssueRegionSelection(imageKind) {
  state.issueSelection = { imageKind };
  toast(`在${imageKind === "src" ? "原图" : "生成图"}上拖拽框选区域`);
}

function handleIssueImageMouseDown(event) {
  const image = event.target.closest(".issueSelectableImage");
  if (!image || !state.issueSelection || image.dataset.imageKind !== state.issueSelection.imageKind) return;
  event.preventDefault();
  const shell = image.closest(".issueImageShell");
  const rect = image.getBoundingClientRect();
  const start = pointInRect(event, rect);
  const box = document.createElement("div");
  box.className = "issueImageSelection";
  shell.appendChild(box);

  const updateBox = (point) => {
    const left = Math.min(start.x, point.x);
    const top = Math.min(start.y, point.y);
    const width = Math.abs(point.x - start.x);
    const height = Math.abs(point.y - start.y);
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;
  };
  const onMove = (moveEvent) => updateBox(pointInRect(moveEvent, rect));
  const onUp = (upEvent) => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    const end = pointInRect(upEvent, rect);
    const bbox = normalizedBbox(start, end, rect);
    box.remove();
    state.issueSelection = null;
    if (bbox.w < 0.005 || bbox.h < 0.005) return;
    insertIssueAnswerText(formatBboxReference(image.dataset.imageKind, bbox));
  };
  updateBox(start);
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function pointInRect(event, rect) {
  return {
    x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)),
    y: Math.max(0, Math.min(rect.height, event.clientY - rect.top)),
  };
}

function normalizedBbox(start, end, rect) {
  const left = Math.min(start.x, end.x);
  const top = Math.min(start.y, end.y);
  return {
    x: left / rect.width,
    y: top / rect.height,
    w: Math.abs(end.x - start.x) / rect.width,
    h: Math.abs(end.y - start.y) / rect.height,
  };
}

function formatBboxReference(imageKind, bbox) {
  return `[${imageKind}: x=${bbox.x.toFixed(3)} y=${bbox.y.toFixed(3)} w=${bbox.w.toFixed(3)} h=${bbox.h.toFixed(3)}]`;
}

function insertIssueAnswerText(text) {
  const input = $("issueAnswerInput");
  if (!input) return;
  const prefix = input.value && !input.value.endsWith("\n") ? "\n" : "";
  input.value = `${input.value}${prefix}${text}`;
  input.focus();
}

async function applySettings() {
  state.threshold = Math.min(5, Math.max(1, Number($("thresholdInput").value || 4)));
  state.nextKey = ($("nextKeyInput").value || "R").slice(0, 1).toUpperCase();
  $("thresholdInput").value = state.threshold;
  $("nextKeyInput").value = state.nextKey;
  localStorage.setItem("annotations.threshold", String(state.threshold));
  localStorage.setItem("annotations.nextKey", state.nextKey);
  if (!$("resultsView").classList.contains("hidden") && state.taskId) {
    await reloadResults();
    renderResultPage();
  }
  toast("设置已生效");
}

function renderResultPage() {
  $("resultsTitle").textContent = `${state.taskName} / 结果展示`;
  if (!state.results.length) {
    $("resultsProgress").textContent = "暂无符合条件的数据";
    $("resultSrcImage").removeAttribute("src");
    $("resultDstImage").removeAttribute("src");
    setImagePath("resultSrcPath", "");
    setImagePath("resultDstPath", "");
    $("resultMeta").innerHTML = "";
    $("resultTags").innerHTML = '<div class="taskMeta">暂无结果</div>';
    return;
  }
  const item = state.results[state.resultPage];
  $("resultsProgress").textContent = `第 ${state.resultPage + 1} / ${state.results.length} 条`;
  $("resultsJumpInput").value = state.resultPage + 1;
  $("resultsJumpInput").max = state.results.length;
  setImagePath("resultSrcPath", imageDisplayPath(item, "src"));
  setImagePath("resultDstPath", imageDisplayPath(item, "dst"));
  preparePreviewImage($("resultSrcImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/src`);
  preparePreviewImage($("resultDstImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/dst`);
  preloadResultNeighbors();
  const reviewers = item.qc_reviewers || [];
  const canUndo = canUndoCurrentResult(item);
  $("resultMeta").innerHTML = `
    <button class="resultBadge editableResultValue" type="button" data-qc-field="mos">MOS ${item.mos}</button>
    <span class="resultBadge">Annotator ${escapeHtml(item.username || "")}</span>
    ${reviewers.length ? `<span class="resultBadge">QC ${escapeHtml(reviewers.join(", "))}</span>` : ""}
    ${canUndo ? '<button class="ghost qcUndoBtn" type="button" data-qc-action="undo">Undo my QC</button>' : ""}
  `;
  renderResultTags(item);
}

function renderResultTags(item) {
  const rows = resultTagRows(item);
  $("resultTags").innerHTML = rows
    .map((row) => `
      <div class="tagRow resultTagRow" data-qc-tag-path="${escapeAttr(JSON.stringify(row.parts || row.path.split(".")))}">
        <div></div>
        <div>
          <div class="tagKey">${escapeHtml(row.path)}</div>
          <button class="resultTagValue editableResultValue" type="button">${escapeHtml(row.value == null || row.value === "" ? "未选择" : String(row.value))}</button>
        </div>
      </div>
    `)
    .join("") || '<div class="taskMeta">No tags</div>';
}

function resultTagRows(item) {
  const rows = flattenTags(item.tags);
  const existing = new Set(rows.map((row) => JSON.stringify(row.parts || row.path.split("."))));
  for (const dimension of state.pairLabelOptions.dimensions || []) {
    const parts = [state.pairLabelOptions.name, dimension.name];
    const key = JSON.stringify(parts);
    if (existing.has(key)) continue;
    rows.push({
      path: parts.join("."),
      parts,
      value: getNested(item.tags || {}, parts),
    });
  }
  return rows;
}

function renderVisualizationPage() {
  $("visualizationTitle").textContent = `${state.taskName} / 文字可视化`;
  if (!state.visualizationTotal || !state.visualizationResults.length) {
    $("visualizationProgress").textContent = "暂无数据";
    $("visualizationSrcImage").removeAttribute("src");
    $("visualizationDstImage").removeAttribute("src");
    setImagePath("visualizationSrcPath", "");
    setImagePath("visualizationDstPath", "");
    $("visualizationPrompts").innerHTML = '<div class="taskMeta">暂无文字描述</div>';
    $("visualizationMeta").innerHTML = "";
    $("visualizationTags").innerHTML = '<div class="taskMeta">暂无标签</div>';
    return;
  }
  const item = state.visualizationResults[0];
  $("visualizationProgress").textContent = `第 ${state.visualizationPage + 1} / ${state.visualizationTotal} 条`;
  $("visualizationJumpInput").value = state.visualizationPage + 1;
  $("visualizationJumpInput").max = state.visualizationTotal;
  setImagePath("visualizationSrcPath", imageDisplayPath(item, "src"));
  setImagePath("visualizationDstPath", imageDisplayPath(item, "dst"));
  preparePreviewImage($("visualizationSrcImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/src`);
  preparePreviewImage($("visualizationDstImage"), `/api/tasks/${state.taskId}/images/${item.item_index}/dst`);
  preloadVisualizationNeighbors();
  renderVisualizationPrompts(item.description_prompts || {});
  $("visualizationMeta").innerHTML = `
    <span class="resultBadge">MOS ${escapeHtml(item.mos == null ? "未打分" : String(item.mos))}</span>
    <span class="resultBadge">Annotator ${escapeHtml(item.username || "")}</span>
  `;
  renderReadonlyTags("visualizationTags", item.tags);
}

function renderVisualizationPrompts(prompts) {
  const sections = [
    ["AIGC文字标注", prompts.adjustment_aigc],
    ["用户提示", prompts.adjustment_user],
    ["思考过程", prompts.thinking],
  ];
  $("visualizationPrompts").innerHTML = sections
    .map(([title, value]) => `
      <section class="promptBlock">
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(value || "暂无")}</p>
      </section>
    `)
    .join("");
}

function imageDisplayPath(item, kind) {
  const prefix = kind === "src" ? "src" : "dst";
  return item?.[`${prefix}_relative_path`] || item?.[`${prefix}_image`] || "";
}

function setImagePath(targetId, value) {
  const target = $(targetId);
  if (!target) return;
  target.textContent = value || "";
  target.title = value || "";
}

function renderReadonlyTags(targetId, tags) {
  const rows = flattenTags(tags);
  $(targetId).innerHTML = rows
    .map((row) => `
      <div class="tagRow resultTagRow readonlyTagRow">
        <div></div>
        <div>
          <div class="tagKey">${escapeHtml(row.path)}</div>
          <div class="resultTagValue">${escapeHtml(String(row.value))}</div>
        </div>
      </div>
    `)
    .join("") || '<div class="taskMeta">No tags</div>';
}

function canUndoCurrentResult(item) {
  return (item.qc_history || []).some((record) => record.username === state.username && !record.undone_at);
}

function openResultsFilterDrawer() {
  state.activeFilterTarget = "results";
  renderResultsFilterDrawer();
  $("resultsFilterOverlay").classList.remove("hidden");
  $("resultsFilterDrawer").classList.remove("hidden");
  $("resultsFilterDrawer").setAttribute("aria-hidden", "false");
}

function openStatsFilterDrawer() {
  state.activeFilterTarget = "stats";
  renderResultsFilterDrawer();
  $("resultsFilterOverlay").classList.remove("hidden");
  $("resultsFilterDrawer").classList.remove("hidden");
  $("resultsFilterDrawer").setAttribute("aria-hidden", "false");
}

function closeResultsFilterDrawer() {
  $("resultsFilterOverlay").classList.add("hidden");
  $("resultsFilterDrawer").classList.add("hidden");
  $("resultsFilterDrawer").setAttribute("aria-hidden", "true");
}

function renderResultsFilterDrawer() {
  const body = $("resultsFilterBody");
  const mosOptions = [1, 2, 3, 4, 5];
  const filterState = activeFilters();
  const filterOptions = activeFilterOptions();
  const annotators = filterOptions.annotators || [];
  const labelOptions = filterOptions.label_options || [];
  $("resultsFilterTitle").textContent = state.activeFilterTarget === "stats" ? "统计筛选" : "结果筛选";
  body.innerHTML = "";
  body.appendChild(renderFilterGroup("MOS 分", mosOptions, "mos", filterState.mos));
  body.appendChild(renderFilterGroup("标注者", annotators, "annotators", filterState.annotators));

  if (!labelOptions.length) {
    const empty = document.createElement("div");
    empty.className = "taskMeta";
    empty.textContent = "暂无标签维度";
    body.appendChild(empty);
    return;
  }

  for (const group of labelOptions) {
    const groupNode = document.createElement("section");
    groupNode.className = "filterSection";
    groupNode.innerHTML = `<h3>${escapeHtml(group.name)}</h3>`;
    for (const dimension of group.dimensions || []) {
      const path = [group.name, dimension.name];
      const key = resultFilterKey(path);
      const selected = normalizeLabelFilterEntry(filterState.labels[key]);
      if (isRangeDimension(dimension)) {
        groupNode.appendChild(renderRangeFilterGroup(dimension.name, selected.ranges, path));
      } else {
        groupNode.appendChild(renderFilterGroup(dimension.name, dimension.options || [], "label", selected.values, path));
      }
    }
    body.appendChild(groupNode);
  }
}

function renderFilterGroup(title, options, type, selectedValues, path = null) {
  const fieldset = document.createElement("fieldset");
  fieldset.className = "filterGroup";
  fieldset.innerHTML = `<legend>${escapeHtml(title)}</legend>`;
  const list = document.createElement("div");
  list.className = "labelOptions";
  if (!options.length) {
    list.innerHTML = '<div class="taskMeta">暂无可选项</div>';
  }
  for (const option of options) {
    const label = document.createElement("label");
    label.className = "labelOption";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.filterType = type;
    input.value = String(option);
    if (path) {
      input.dataset.labelPath = JSON.stringify(path);
    }
    input.checked = selectedValues.some((value) => isSameValue(value, option));
    const span = document.createElement("span");
    span.textContent = String(option);
    label.appendChild(input);
    label.appendChild(span);
    list.appendChild(label);
  }
  fieldset.appendChild(list);
  return fieldset;
}

function renderRangeFilterGroup(title, ranges, path) {
  const fieldset = document.createElement("fieldset");
  fieldset.className = "filterGroup";
  fieldset.innerHTML = `<legend>${escapeHtml(title)}</legend>`;
  const range = ranges[0] || {};
  const row = document.createElement("div");
  row.className = "rangeFilter";
  row.innerHTML = `
    <input type="number" placeholder="最小值" data-filter-type="label-range" data-range-bound="min" data-label-path="${escapeAttr(JSON.stringify(path))}" value="${escapeAttr(range.min ?? "")}">
    <span>至</span>
    <input type="number" placeholder="最大值" data-filter-type="label-range" data-range-bound="max" data-label-path="${escapeAttr(JSON.stringify(path))}" value="${escapeAttr(range.max ?? "")}">
  `;
  fieldset.appendChild(row);
  return fieldset;
}

function collectResultsFilters() {
  const filters = emptyFilters();
  $("resultsFilterBody").querySelectorAll('input[type="checkbox"]:checked').forEach((input) => {
    const value = input.value;
    if (input.dataset.filterType === "mos") {
      filters.mos.push(Number(value));
      return;
    }
    if (input.dataset.filterType === "annotators") {
      filters.annotators.push(value);
      return;
    }
    if (input.dataset.filterType === "label") {
      const path = JSON.parse(input.dataset.labelPath || "[]");
      const key = resultFilterKey(path);
      filters.labels[key] = normalizeLabelFilterEntry(filters.labels[key]);
      filters.labels[key].values.push(parseValue(value));
    }
  });
  const rangesByKey = new Map();
  $("resultsFilterBody").querySelectorAll('input[data-filter-type="label-range"]').forEach((input) => {
    const path = JSON.parse(input.dataset.labelPath || "[]");
    const key = resultFilterKey(path);
    const entry = rangesByKey.get(key) || { min: "", max: "" };
    entry[input.dataset.rangeBound] = input.value.trim();
    rangesByKey.set(key, entry);
  });
  for (const [key, range] of rangesByKey.entries()) {
    if (range.min === "" && range.max === "") continue;
    filters.labels[key] = normalizeLabelFilterEntry(filters.labels[key]);
    filters.labels[key].ranges.push({
      min: range.min === "" ? null : Number(range.min),
      max: range.max === "" ? null : Number(range.max),
    });
  }
  return filters;
}

function buildFilterPayload(filterState) {
  return {
    mos: filterState.mos || [],
    annotators: filterState.annotators || [],
    labels: Object.entries(filterState.labels || {}).map(([key, entry]) => {
      const normalized = normalizeLabelFilterEntry(entry);
      return {
        path: JSON.parse(key),
        values: normalized.values,
        ranges: normalized.ranges,
      };
    }),
  };
}

function hasActiveResultFilters(filters) {
  return Boolean(filters.mos.length || filters.annotators.length || filters.labels.some((filter) => filter.values.length || filter.ranges.length));
}

function resultFilterKey(path) {
  return JSON.stringify(path);
}

async function applyResultsFilter() {
  if (state.activeFilterTarget === "stats") {
    state.statsFilters = collectResultsFilters();
    await reloadStatistics();
  } else {
    state.resultFilters = collectResultsFilters();
    state.resultPage = 0;
    await reloadResults();
  }
  closeResultsFilterDrawer();
  if (state.activeFilterTarget === "stats") {
    renderStatisticsPage();
  } else {
    renderResultPage();
  }
}

async function resetResultsFilter() {
  if (state.activeFilterTarget === "stats") {
    state.statsFilters = emptyFilters();
  } else {
    state.resultFilters = emptyFilters();
    state.resultPage = 0;
  }
  renderResultsFilterDrawer();
  if (state.activeFilterTarget === "stats") {
    await reloadStatistics();
    renderStatisticsPage();
  } else {
    await reloadResults();
    renderResultPage();
  }
}

function renderStatisticsPage() {
  const statistics = state.statistics;
  $("statsTitle").textContent = `${state.taskName} / 结果统计`;
  if (!statistics) {
    $("statsSummary").textContent = "暂无统计数据";
    $("statsCharts").innerHTML = "";
    $("statsCombinations").innerHTML = "";
    $("statsDimensionList").innerHTML = "";
    return;
  }
  $("statsSummary").textContent = `当前统计 ${statistics.total || 0} 条已标注数据`;
  renderStatsDimensionList(statistics.available_dimensions || []);
  const chartGroups = [
    statistics.annotators,
    statistics.mos,
    ...(statistics.labels || []),
  ].filter((group) => group?.items?.length);
  renderStatsChartToggle();
  $("statsCharts").innerHTML = chartGroups.map(renderStatsCard).join("") || '<div class="taskMeta">暂无可统计数据</div>';
  $("statsCombinations").innerHTML = (statistics.combinations || []).map(renderStatsCard).join("");
}

function renderStatsChartToggle() {
  $("statsBarChartBtn").classList.toggle("active", state.statsChartType === "bar");
  $("statsPieChartBtn").classList.toggle("active", state.statsChartType === "pie");
  $("statsBarChartBtn").setAttribute("aria-pressed", String(state.statsChartType === "bar"));
  $("statsPieChartBtn").setAttribute("aria-pressed", String(state.statsChartType === "pie"));
}

function setStatsChartType(chartType) {
  if (!["bar", "pie"].includes(chartType)) return;
  state.statsChartType = chartType;
  localStorage.setItem("annotations.statsChartType", chartType);
  renderStatisticsPage();
}

function renderStatsDimensionList(dimensions) {
  const selectedKeys = new Set(state.statsCombinations[0]?.map(dimensionIdentity) || []);
  $("statsDimensionList").innerHTML = dimensions.map((dimension) => {
    const key = dimensionIdentity(dimension);
    return `
      <label class="statsDimensionOption">
        <input type="checkbox" data-dimension="${escapeAttr(JSON.stringify(dimension))}" ${selectedKeys.has(key) ? "checked" : ""}>
        <span>${escapeHtml(dimension.label || dimension.title || key)}</span>
      </label>
    `;
  }).join("");
}

function renderStatsCard(group) {
  const items = group.items || [];
  const maxCount = Math.max(1, ...items.map((item) => item.count || 0));
  const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0);
  return `
    <section class="statsCard">
      <div class="statsCardHeader">
        <h3>${escapeHtml(group.title || "统计")}</h3>
        <span>${total} 条</span>
      </div>
      ${state.statsChartType === "pie" ? renderStatsPie(items, total) : renderStatsBars(items, maxCount, total)}
    </section>
  `;
}

function renderStatsBars(items, maxCount, total) {
  return `
    <div class="statsBars">
      ${items.map((item) => renderStatsBar(item, maxCount, total)).join("")}
    </div>
  `;
}

function renderStatsBar(item, maxCount, total) {
  const barPercent = Math.max(2, Math.round((Number(item.count || 0) / maxCount) * 100));
  const sharePercent = formatPercent(item.count, total);
  return `
    <div class="statsBarRow">
      <div class="statsBarLabel" title="${escapeAttr(item.label)}">${escapeHtml(item.label)}</div>
      <div class="statsBarTrack"><div class="statsBarFill" style="width:${barPercent}%"></div></div>
      <div class="statsBarCount">${item.count}<span>${sharePercent}</span></div>
    </div>
  `;
}

function renderStatsPie(items, total) {
  if (!total) {
    return '<div class="taskMeta">暂无可统计数据</div>';
  }
  const palette = ["#2457a6", "#0f7b6c", "#c47f13", "#b42318", "#725ac1", "#2f7d32", "#a23f72", "#54616f"];
  let start = 0;
  const segments = items.map((item, index) => {
    const count = Number(item.count || 0);
    const angle = total ? (count / total) * 360 : 0;
    const segment = `${escapeAttr(palette[index % palette.length])} ${start}deg ${start + angle}deg`;
    start += angle;
    return segment;
  });
  return `
    <div class="statsPieWrap">
      <div class="statsPie" style="background: conic-gradient(${segments.join(", ")});"></div>
      <div class="statsPieLegend">
        ${items.map((item, index) => `
          <div class="statsPieLegendRow">
            <span class="statsPieSwatch" style="background:${escapeAttr(palette[index % palette.length])}"></span>
            <span class="statsPieLabel" title="${escapeAttr(item.label)}">${escapeHtml(item.label)}</span>
            <span class="statsPieValue">${formatStatsLegendValue(item.count, total)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function formatStatsLegendValue(count, total) {
  return `${formatStatsLegendPercent(count, total)} (${Number(count || 0)})`;
}

function formatStatsLegendPercent(count, total) {
  const percent = total ? (Number(count || 0) / total) * 100 : 0;
  return `${percent.toFixed(2)}%`;
}

function formatPercent(count, total) {
  const percent = total ? (Number(count || 0) / total) * 100 : 0;
  return `${percent >= 10 ? percent.toFixed(1) : percent.toFixed(2)}%`;
}

function collectStatsCombination() {
  const selected = [];
  $("statsDimensionList").querySelectorAll('input[type="checkbox"]:checked').forEach((input) => {
    if (selected.length >= 3) return;
    selected.push(JSON.parse(input.dataset.dimension));
  });
  return selected;
}

async function applyStatsCombination() {
  const selected = collectStatsCombination();
  if (selected.length < 2) {
    toast("请至少选择 2 个组合维度");
    return;
  }
  state.statsCombinations = [selected];
  await reloadStatistics();
  renderStatisticsPage();
}

function dimensionIdentity(dimension) {
  if (dimension.type === "label") {
    return `label:${JSON.stringify(dimension.path || [])}`;
  }
  return String(dimension.type || "");
}

function activeFilters() {
  return state.activeFilterTarget === "stats" ? state.statsFilters : state.resultFilters;
}

function activeFilterOptions() {
  if (state.activeFilterTarget === "stats" && state.statistics?.filter_options) {
    return state.statistics.filter_options;
  }
  return state.resultFilterOptions;
}

function updatePairLabelOptionsFromFilterOptions(filterOptions) {
  const pairGroup = (filterOptions?.label_options || []).find((group) => group.name === state.pairLabelOptions.name);
  if (pairGroup) {
    state.pairLabelOptions = deepClone(pairGroup);
  }
}

function emptyFilters() {
  return { mos: [], annotators: [], labels: {} };
}

function normalizeLabelFilterEntry(entry) {
  if (Array.isArray(entry)) {
    return { values: entry, ranges: [] };
  }
  return {
    values: entry?.values || [],
    ranges: entry?.ranges || [],
  };
}

function isRangeDimension(dimension) {
  const name = String(dimension?.name || "");
  const options = dimension?.options || [];
  return name.includes("角度") && options.length > 0 && options.every((option) => !Number.isNaN(Number(option)));
}


function currentResultItem() {
  return state.results[state.resultPage];
}

function findResultDimension(path) {
  for (const group of state.resultFilterOptions.label_options || []) {
    if (!isSameValue(group.name, path[0])) continue;
    for (const dimension of group.dimensions || []) {
      if (isSameValue(dimension.name, path[1])) {
        return dimension;
      }
    }
  }
  return null;
}

function beginResultMosEdit(button) {
  const item = currentResultItem();
  if (!item) return;
  const select = document.createElement("select");
  select.className = "qcInlineEditor";
  [1, 2, 3, 4, 5].forEach((score) => {
    const option = document.createElement("option");
    option.value = String(score);
    option.textContent = `MOS ${score}`;
    option.selected = Number(item.mos) === score;
    select.appendChild(option);
  });
  button.replaceWith(select);
  select.focus();
  select.addEventListener("change", () => saveResultQualityCheck(Number(select.value), deepClone(item.tags)));
  select.addEventListener("blur", () => renderResultPage(), { once: true });
}

function beginResultTagEdit(row) {
  const item = currentResultItem();
  if (!item) return;
  const path = JSON.parse(row.dataset.qcTagPath || "[]");
  const valueButton = row.querySelector(".resultTagValue");
  if (!valueButton) return;
  const currentValue = getNested(item.tags || {}, path);
  const dimension = findResultDimension(path);
  const editor = isRangeDimension(dimension)
    ? renderResultNumberEditor(currentValue)
    : renderResultValueEditor(dimension?.options || [], currentValue);
  valueButton.replaceWith(editor);
  editor.focus();
  let saved = false;
  const save = () => {
    if (saved) return;
    saved = true;
    const nextTags = deepClone(item.tags);
    setNested(nextTags, path, parseValue(editor.value));
    saveResultQualityCheck(Number(item.mos), nextTags).catch((error) => toast(error.message));
  };
  editor.addEventListener("change", save);
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") renderResultPage();
  });
  editor.addEventListener("blur", save, { once: true });
}

function renderResultNumberEditor(currentValue) {
  const input = document.createElement("input");
  input.className = "qcInlineEditor qcNumberEditor";
  input.type = "number";
  input.step = "1";
  input.inputMode = "numeric";
  input.value = currentValue ?? "";
  return input;
}

function renderResultValueEditor(options, currentValue) {
  if (!options.length) {
    const input = document.createElement("input");
    input.className = "qcInlineEditor";
    input.value = currentValue ?? "";
    return input;
  }
  return renderResultSelectEditor(options, currentValue);
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
    option.selected = isSelectedValue(optionValue, currentValue);
    select.appendChild(option);
  }
  return select;
}

async function saveResultQualityCheck(mos, tags) {
  const item = currentResultItem();
  if (!item) return;
  const data = await api(`/api/tasks/${state.taskId}/results/${item.item_index}/qc`, {
    method: "POST",
    body: JSON.stringify({
      username: state.username,
      mos,
      tags,
    }),
  });
  Object.assign(item, data.annotation);
  renderResultPage();
  toast("QC saved");
}

async function undoCurrentQualityCheck() {
  const item = currentResultItem();
  if (!item) return;
  const data = await api(`/api/tasks/${state.taskId}/results/${item.item_index}/qc`, {
    method: "DELETE",
    body: JSON.stringify({ username: state.username }),
  });
  Object.assign(item, data.annotation);
  renderResultPage();
  toast("QC undone");
}

function preparePreviewImage(image, previewSrc) {
  image.loading = "eager";
  image.decoding = "async";
  image.fetchPriority = "high";
  if (image.src !== previewSrc) {
    image.src = previewSrc;
  }
  image.dataset.originalSrc = `${previewSrc}?original=1`;
  image.classList.remove("fullResolution");
  image.title = "点击加载原始分辨率";
}

function preloadAnnotationNeighbors() {
  if (!state.subtask?.items?.length) return;
  preloadNeighborItems(state.subtask.items, state.page);
}

function preloadResultNeighbors() {
  if (!state.results.length) return;
  preloadNeighborItems(state.results, state.resultPage);
}

function preloadVisualizationNeighbors() {
  if (!state.visualizationTotal) return;
  [state.visualizationPage + 1, state.visualizationPage - 1, state.visualizationPage + 2].forEach((index) => {
    if (index < 0 || index >= state.visualizationTotal) return;
    preloadImage(`/api/tasks/${state.taskId}/images/${index}/src`);
    preloadImage(`/api/tasks/${state.taskId}/images/${index}/dst`);
  });
}

function preloadNeighborItems(items, page) {
  [page + 1, page - 1, page + 2].forEach((index) => {
    const item = items[index];
    if (!item) return;
    preloadImage(`/api/tasks/${state.taskId}/images/${item.item_index}/src`);
    preloadImage(`/api/tasks/${state.taskId}/images/${item.item_index}/dst`);
  });
}

function preloadImage(src) {
  if (preloadedImages.has(src)) return;
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

function loadOriginalImage(image) {
  const originalSrc = image.dataset.originalSrc;
  if (!originalSrc || image.src.endsWith("?original=1")) return;
  image.src = originalSrc;
  image.classList.add("fullResolution");
  image.title = "已加载原始分辨率";
}

function downloadTask(taskId, format) {
  window.location.href = `/api/tasks/${taskId}/download?format=${format}`;
}

function flattenTags(value, prefix = "", parts = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return prefix ? [{ path: prefix, parts, value }] : [];
  }
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    const childParts = [...parts, key];
    if (child && typeof child === "object" && !Array.isArray(child)) {
      return flattenTags(child, path, childParts);
    }
    return [{ path, parts: childParts, value: child }];
  });
}

function setNested(target, parts, value) {
  let cursor = target;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      cursor[part] = value;
    } else {
      cursor[part] = cursor[part] || {};
      cursor = cursor[part];
    }
  });
}

function getNested(source, parts) {
  let cursor = source;
  for (const part of parts) {
    if (!cursor || typeof cursor !== "object" || !(part in cursor)) return undefined;
    cursor = cursor[part];
  }
  return cursor;
}

function optionsWithCurrentValue(options, currentValue) {
  const currentValues = Array.isArray(currentValue) ? currentValue : [currentValue];
  const missing = currentValues.filter(
    (value) => value !== undefined && !options.some((option) => isSameValue(option, value)),
  );
  return [...missing, ...options];
}

function isSelectedValue(option, currentValue) {
  if (Array.isArray(currentValue)) {
    return currentValue.length > 0 && isSameValue(option, currentValue[0]);
  }
  return isSameValue(option, currentValue);
}

function isSameValue(left, right) {
  return String(left) === String(right);
}

function hashText(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash) + value.charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash).toString(36);
}

function parseValue(value) {
  const trimmed = value.trim();
  if (trimmed !== "" && !Number.isNaN(Number(trimmed))) {
    return Number(trimmed);
  }
  return value;
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/"/g, '\\"');
}

function bindEvents() {
  $("loginForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const username = $("usernameInput").value.trim();
    if (!username) return toast("请输入用户名");
    state.username = username;
    localStorage.setItem("annotations.username", username);
    updateSession();
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("logoutBtn").addEventListener("click", () => {
    localStorage.removeItem("annotations.username");
    state.username = "";
    updateSession();
    show("loginView");
  });
  $("thresholdInput").addEventListener("change", () => {
    state.threshold = Math.min(5, Math.max(1, Number($("thresholdInput").value || 4)));
    localStorage.setItem("annotations.threshold", String(state.threshold));
  });
  $("nextKeyInput").addEventListener("change", () => {
    state.nextKey = ($("nextKeyInput").value || "R").slice(0, 1).toUpperCase();
    $("nextKeyInput").value = state.nextKey;
    localStorage.setItem("annotations.nextKey", state.nextKey);
  });
  $("applySettingsBtn").addEventListener("click", () => applySettings().catch((error) => toast(error.message)));
  $("createTaskForm").addEventListener("submit", (event) => createTask(event).catch((error) => toast(error.message)));
  $("refreshTasksBtn").addEventListener("click", () => loadTasks().catch((error) => toast(error.message)));
  $("taskList").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const taskId = button.dataset.id;
    const taskName = button.dataset.name;
    if (button.dataset.action === "annotate") startAnnotation(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "results") openResults(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "issues") openIssues(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "visualization") openVisualization(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "stats") openStatistics(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "refresh-labels") refreshTaskLabels(taskId, button).catch((error) => toast(error.message));
    if (button.dataset.action === "cache-inputs") warmInputPreviewCache(taskId, button).catch((error) => toast(error.message));
    if (button.dataset.action === "download-jsonl") downloadTask(taskId, "jsonl");
    if (button.dataset.action === "download-xlsx") downloadTask(taskId, "xlsx");
    if (button.dataset.action === "delete") deleteTask(taskId, taskName).catch((error) => toast(error.message));
  });
  $("backHomeBtn").addEventListener("click", () => {
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("abandonSubtaskBtn").addEventListener("click", () => abandonSubtask().catch((error) => toast(error.message)));
  $("backFromResultsBtn").addEventListener("click", () => {
    closeResultsFilterDrawer();
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("backFromIssuesBtn").addEventListener("click", () => {
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("backFromVisualizationBtn").addEventListener("click", () => {
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("backFromStatsBtn").addEventListener("click", () => {
    closeResultsFilterDrawer();
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("openResultsFilterBtn").addEventListener("click", () => openResultsFilterDrawer());
  $("createIssueBtn").addEventListener("click", () => openIssueModal());
  $("cancelIssueBtn").addEventListener("click", () => closeIssueModal());
  $("issueForm").addEventListener("submit", (event) => submitResultIssue(event).catch((error) => toast(error.message)));
  $("exportIssuesBtn").addEventListener("click", () => exportIssuesMarkdown());
  $("issuesList").addEventListener("click", (event) => {
    const button = event.target.closest(".issueListItem");
    if (!button) return;
    state.activeIssueId = button.dataset.issueId;
    renderIssuesPage();
  });
  $("issueDetail").addEventListener("click", (event) => {
    const button = event.target.closest("[data-issue-action]");
    if (!button) return;
    const action = button.dataset.issueAction;
    const issue = state.issues.find((item) => item.id === state.activeIssueId);
    if (action === "open-result") openIssueResult(issue).catch((error) => toast(error.message));
    if (action === "close") setIssueStatus("close").catch((error) => toast(error.message));
    if (action === "reopen") setIssueStatus("reopen").catch((error) => toast(error.message));
    if (action === "answer") submitIssueAnswer().catch((error) => toast(error.message));
    if (action === "select-src") beginIssueRegionSelection("src");
    if (action === "select-dst") beginIssueRegionSelection("dst");
  });
  $("issueDetail").addEventListener("mousedown", (event) => handleIssueImageMouseDown(event));
  $("statsBarChartBtn").addEventListener("click", () => setStatsChartType("bar"));
  $("statsPieChartBtn").addEventListener("click", () => setStatsChartType("pie"));
  $("openStatsFilterBtn").addEventListener("click", () => openStatsFilterDrawer());
  $("applyStatsCombinationBtn").addEventListener("click", () => applyStatsCombination().catch((error) => toast(error.message)));
  $("closeResultsFilterBtn").addEventListener("click", () => closeResultsFilterDrawer());
  $("resultsFilterOverlay").addEventListener("click", () => closeResultsFilterDrawer());
  $("applyResultsFilterBtn").addEventListener("click", () => applyResultsFilter().catch((error) => toast(error.message)));
  $("resetResultsFilterBtn").addEventListener("click", () => resetResultsFilter().catch((error) => toast(error.message)));
  $("resultMeta").addEventListener("click", (event) => {
    const mosButton = event.target.closest('[data-qc-field="mos"]');
    if (mosButton) {
      beginResultMosEdit(mosButton);
      return;
    }
    const undoButton = event.target.closest('[data-qc-action="undo"]');
    if (undoButton) {
      undoCurrentQualityCheck().catch((error) => toast(error.message));
    }
  });
  $("resultTags").addEventListener("click", (event) => {
    const row = event.target.closest(".resultTagRow");
    if (row && !event.target.closest(".qcInlineEditor")) {
      beginResultTagEdit(row);
    }
  });
  $("prevBtn").addEventListener("click", () => movePage(-1));
  $("nextBtn").addEventListener("click", () => saveAndMove(1));
  $("tagList").addEventListener("click", (event) => {
    const button = event.target.closest(".pairAddBtn");
    if (!button) return;
    const dimensionName = button.dataset.pairDimension;
    const input = Array.from($("tagList").querySelectorAll(".pairAddInput"))
      .find((node) => node.dataset.pairDimension === dimensionName);
    addPairLabelOption(dimensionName, input?.value || "").catch((error) => toast(error.message));
  });
  $("jumpBtn").addEventListener("click", async () => {
    try {
      await saveCurrent();
      const target = Number($("jumpInput").value) - 1;
      if (target >= 0 && target < state.subtask.items.length) {
        state.page = target;
        renderAnnotationPage();
      }
    } catch (error) {
      toast(error.message);
    }
  });
  $("resultsPrevBtn").addEventListener("click", () => {
    if (state.resultPage > 0) {
      state.resultPage -= 1;
      renderResultPage();
    }
  });
  $("resultsNextBtn").addEventListener("click", () => {
    if (state.resultPage < state.results.length - 1) {
      state.resultPage += 1;
      renderResultPage();
    }
  });
  $("resultsJumpBtn").addEventListener("click", () => {
    const target = Number($("resultsJumpInput").value) - 1;
    if (target >= 0 && target < state.results.length) {
      state.resultPage = target;
      renderResultPage();
    }
  });
  $("visualizationPrevBtn").addEventListener("click", () => {
    if (state.visualizationPage > 0) {
      state.visualizationPage -= 1;
      reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
    }
  });
  $("visualizationNextBtn").addEventListener("click", () => {
    if (state.visualizationPage < state.visualizationTotal - 1) {
      state.visualizationPage += 1;
      reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
    }
  });
  $("visualizationJumpBtn").addEventListener("click", () => {
    const target = Number($("visualizationJumpInput").value) - 1;
    if (target >= 0 && target < state.visualizationTotal) {
      state.visualizationPage = target;
      reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
    }
  });
  ["srcImage", "dstImage", "resultSrcImage", "resultDstImage", "visualizationSrcImage", "visualizationDstImage"].forEach((id) => {
    $(id).addEventListener("click", () => loadOriginalImage($(id)));
  });
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA"].includes(event.target.tagName)) return;
    if (!$("visualizationView").classList.contains("hidden")) {
      if (event.key === "ArrowLeft" && state.visualizationPage > 0) {
        state.visualizationPage -= 1;
        reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
      }
      if (event.key === "ArrowRight" && state.visualizationPage < state.visualizationTotal - 1) {
        state.visualizationPage += 1;
        reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
      }
      if (event.key.toUpperCase() === state.nextKey.toUpperCase() && state.visualizationPage < state.visualizationTotal - 1) {
        state.visualizationPage += 1;
        reloadVisualizationResults().then(renderVisualizationPage).catch((error) => toast(error.message));
      }
      return;
    }
    if (!$("resultsView").classList.contains("hidden")) {
      if (event.key === "ArrowLeft" && state.resultPage > 0) {
        state.resultPage -= 1;
        renderResultPage();
      }
      if (event.key === "ArrowRight" && state.resultPage < state.results.length - 1) {
        state.resultPage += 1;
        renderResultPage();
      }
      if (event.key.toUpperCase() === state.nextKey.toUpperCase() && state.resultPage < state.results.length - 1) {
        state.resultPage += 1;
        renderResultPage();
      }
      return;
    }
    if ($("annotateView").classList.contains("hidden")) return;
    if (event.key === "ArrowLeft") {
      movePage(-1);
      return;
    }
    if (event.key === "ArrowRight") {
      saveAndMove(1);
      return;
    }
    if (/^[1-5]$/.test(event.key)) {
      chooseMos(Number(event.key));
    }
    if (event.key.toUpperCase() === state.nextKey.toUpperCase()) {
      saveAndMove(1);
    }
  });
}

bindEvents();
requireLogin();
if (state.username) {
  loadTasks().catch((error) => toast(error.message));
}
