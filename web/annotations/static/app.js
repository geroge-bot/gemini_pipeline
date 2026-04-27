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
  resultFilterOptions: { mos: [], annotators: [], label_options: [] },
  resultFilters: { mos: [], annotators: [], labels: {} },
  statistics: null,
  statsFilters: { mos: [], annotators: [], labels: {} },
  statsCombinations: [],
  activeFilterTarget: "results",
};

const preloadedImages = new Map();
const MAX_PRELOADED_IMAGES = 80;

const $ = (id) => document.getElementById(id);

function show(viewId) {
  ["loginView", "homeView", "annotateView", "resultsView", "statsView"].forEach((id) => {
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
        <button class="ghost" data-action="stats" data-id="${task.id}" data-name="${escapeAttr(task.name)}">结果统计</button>
        <button class="ghost" data-action="download-jsonl" data-id="${task.id}">下载 JSONL</button>
        <button class="ghost" data-action="download-xlsx" data-id="${task.id}">下载 Excel</button>
        <button class="dangerBtn" data-action="delete" data-id="${task.id}" data-name="${escapeAttr(task.name)}">删除任务</button>
        <button class="ghost" data-action="refresh-labels" data-id="${task.id}">更新AI标签</button>
      </div>
    `;
    list.appendChild(card);
  }
}

async function deleteTask(taskId, taskName) {
  if (!window.confirm(`确认删除任务“${taskName}”？该操作会删除任务分配和标注结果。`)) {
    return;
  }
  await api(`/api/tasks/${taskId}`, { method: "DELETE" });
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

async function createTask(event) {
  event.preventDefault();
  const submitButton = $("createTaskForm").querySelector('button[type="submit"]');
  const payload = {
    name: $("taskNameInput").value.trim(),
    root_dir: $("rootDirInput").value.trim(),
    annotation_dir: $("annotationDirInput").value.trim(),
    jsonl_path: $("jsonlPathInput").value.trim(),
    chunk_size: Number($("chunkSizeInput").value || 100),
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
    list.innerHTML = '<div class="taskMeta">当前任务没有可展示的标签维度</div>';
    return;
  }
  for (const group of groups) {
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
  if (state.resultPage >= state.results.length) {
    state.resultPage = Math.max(0, state.results.length - 1);
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
  }
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
    $("resultMeta").innerHTML = "";
    $("resultTags").innerHTML = '<div class="taskMeta">暂无结果</div>';
    return;
  }
  const item = state.results[state.resultPage];
  $("resultsProgress").textContent = `第 ${state.resultPage + 1} / ${state.results.length} 条`;
  $("resultsJumpInput").value = state.resultPage + 1;
  $("resultsJumpInput").max = state.results.length;
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
  const rows = flattenTags(item.tags);
  $("resultTags").innerHTML = rows
    .map((row) => `
      <div class="tagRow resultTagRow" data-qc-tag-path="${escapeAttr(JSON.stringify(row.parts || row.path.split(".")))}">
        <div></div>
        <div>
          <div class="tagKey">${escapeHtml(row.path)}</div>
          <button class="resultTagValue editableResultValue" type="button">${escapeHtml(String(row.value))}</button>
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
  $("statsCharts").innerHTML = chartGroups.map(renderStatsCard).join("") || '<div class="taskMeta">暂无可统计数据</div>';
  $("statsCombinations").innerHTML = (statistics.combinations || []).map(renderStatsCard).join("");
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
  return `
    <section class="statsCard">
      <div class="statsCardHeader">
        <h3>${escapeHtml(group.title || "统计")}</h3>
        <span>${items.reduce((sum, item) => sum + Number(item.count || 0), 0)} 条</span>
      </div>
      <div class="statsBars">
        ${items.map((item) => renderStatsBar(item, maxCount)).join("")}
      </div>
    </section>
  `;
}

function renderStatsBar(item, maxCount) {
  const percent = Math.max(2, Math.round((Number(item.count || 0) / maxCount) * 100));
  return `
    <div class="statsBarRow">
      <div class="statsBarLabel" title="${escapeAttr(item.label)}">${escapeHtml(item.label)}</div>
      <div class="statsBarTrack"><div class="statsBarFill" style="width:${percent}%"></div></div>
      <div class="statsBarCount">${item.count}</div>
    </div>
  `;
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
    if (button.dataset.action === "stats") openStatistics(taskId, taskName).catch((error) => toast(error.message));
    if (button.dataset.action === "refresh-labels") refreshTaskLabels(taskId, button).catch((error) => toast(error.message));
    if (button.dataset.action === "download-jsonl") downloadTask(taskId, "jsonl");
    if (button.dataset.action === "download-xlsx") downloadTask(taskId, "xlsx");
    if (button.dataset.action === "delete") deleteTask(taskId, taskName).catch((error) => toast(error.message));
  });
  $("backHomeBtn").addEventListener("click", () => {
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("backFromResultsBtn").addEventListener("click", () => {
    closeResultsFilterDrawer();
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("backFromStatsBtn").addEventListener("click", () => {
    closeResultsFilterDrawer();
    show("homeView");
    loadTasks().catch((error) => toast(error.message));
  });
  $("openResultsFilterBtn").addEventListener("click", () => openResultsFilterDrawer());
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
  ["srcImage", "dstImage", "resultSrcImage", "resultDstImage"].forEach((id) => {
    $(id).addEventListener("click", () => loadOriginalImage($(id)));
  });
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA"].includes(event.target.tagName)) return;
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
