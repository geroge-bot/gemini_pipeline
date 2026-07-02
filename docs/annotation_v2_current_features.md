# Annotation V2 当前已有功能总结

本文基于当前 `web/annotations_v2` 实现整理，描述 annotations v2 已具备的功能、页面/API 行为、数据流，以及图片与页面加载速度相关需求。历史设计文档中的待办项如与当前实现不一致，以本文为准。

## 1. 功能定位

annotations v2 是独立于旧版 `web/annotations` 的本地 Flask 标注工作台，用于把图片对数据推进为完整的多阶段标注流水线：

1. 创建任务并导入图片对、已有标签、生图 prompt。
2. 第一轮粗筛，对图片对做 MOS 和瑕疵判断。
3. 第二轮精筛，只处理粗筛通过的数据。
4. 基于标签分布从精筛通过数据中采样。
5. 对采样数据做标签纠错。
6. 导入外部标注结果，导出最终 JSONL。
7. 按阶段查看、筛选和复查结果。

v2 仍采用本地文件持久化：全局任务列表写入 `state.json`，每个任务的条目写入 `items.json`，阶段记录写入 `records.json` 或 `records/*.json.gz` 分片，任务进度快照写入 `summary.json`。不依赖数据库。

## 2. 任务与配置

### 2.1 任务创建

首页提供“新建任务”表单，支持输入：

- 任务名称。
- 根目录 `root_dir`。
- 图片对 JSONL 路径 `jsonl_path`。
- 标签目录 `label_dir`，可留空。
- 生图 Prompt JSON 根目录 `generation_prompt_dir`，可留空。
- 粗筛阈值，默认 `MOS >= 4`。
- 精筛阈值，默认 `MOS >= 4`。
- 粗筛标注人数，默认 1。
- 精筛标注人数，默认 1。
- 精筛是否记录瑕疵。
- 粗筛问题类型，用逗号或换行分隔。
- 需要纠错的标签路径，例如 `输入图/菜品种类`。

创建时后端读取 JSONL，每行必须是 JSON 对象，并且至少包含 `src_image` 与 `dst_image`。若 `root_dir` 未填，默认使用 JSONL 所在目录。若提供 `label_dir`，系统会按原图和目标图路径寻找同名 `.json` 标签文件，将标签合并到条目中。

### 2.2 标签清洗

系统会引用旧版标准标签配置 `LABEL_OPTION_GROUPS`，仅保留标准标签组和标准维度。导入数据、标签目录和外部结果中的非标准字段会被过滤，例如带 `_alt`、`_v2`、未知维度等污染字段不会进入任务标签。

当前支持的标签来源包括：

- JSONL 行内 `labels`。
- 标签目录下与图片相对路径同名的 `.json`。
- 外部导入 JSONL 的 `object_labels`、`labels`、`original_labels`。

### 2.3 生图 Prompt 加载

任务可配置 `generation_prompt_dir`。系统会根据 `dst_image` 推导对应 `.json` 路径，读取其中的 `original_plan`，并保存为：

- `generation_prompt`。
- `generation_prompt_json_path`。

匹配策略包含：

- 完整相对路径匹配。
- 去掉首层目录后的路径匹配。
- 文件名兜底匹配。

如果 `original_plan` 是字符串，直接展示；如果是对象或数组，格式化为 JSON 字符串。标注页和可视化页都会以折叠区展示生图 Prompt，并支持简单 Markdown 渲染。

### 2.4 任务编辑

任务创建后可在首页点击“编辑”，修改：

- 粗筛问题类型。
- 标签纠错路径。
- 生图 Prompt JSON 根目录。

编辑只更新任务配置，不重写既有粗筛、精筛、采样或标签纠错记录。更新 `generation_prompt_dir` 时，会刷新已有 `items.json` 中的 prompt 字段。

### 2.5 任务删除

首页提供删除入口，但只有用户名为 `孙本猿` 的用户可以删除任务。删除操作只从任务列表中移除任务，不删除任务数据目录、`items.json`、`records.json` 或原始图片。

## 3. 用户与任务列表

所有页面都有登录态入口。用户名保存在浏览器 `localStorage` 的 `annotations_v2.username` 中。系统没有密码或服务端鉴权，用户名主要用于：

- 区分粗筛/精筛多标注人记录。
- 分配标签纠错队列。
- 标记导出结果中的标注者。
- 控制任务删除权限。

首页任务列表展示每个任务的阶段进度：

- 数据总数。
- 粗筛每一轮标注完成进度和粗筛通过数。
- 精筛每一轮标注完成进度和精筛通过数。
- 采样数。
- 标签纠错完成数。

任务列表接口 `/api/tasks` 读取每个任务的 `summary.json` 快照，不在首屏同步全量扫描 `items.json` 和 records。首页会先显示快照，再异步请求 `/api/tasks/<task_id>/summary` 刷新进度；刷新后的 summary 会回写 `summary.json`。保存单条标注时只把快照标记为 `stale`，避免“保存并进入下一条”的高频链路被全量统计阻塞。导入和采样这类本来就会批量扫描的操作会同步刷新 summary 快照。

进度卡片可直接进入对应阶段页面。任务卡片还提供：

- 粗筛结果、精筛结果、采样结果、标签结果可视化入口。
- 编辑入口。
- 导入入口。
- 缓存图片入口。
- 导出入口。
- 管理员删除入口。

## 4. 粗筛功能

粗筛页面位于 `/dataset/rate/<task_id>?stage=rough`。队列默认包含任务中的所有图片对。

进入粗筛、精筛或标签纠错页时，前端会直接请求 `/api/tasks/<task_id>` 获取单任务元信息，不再先等待 `/api/tasks` 返回全部任务。随后页面请求 `/api/tasks/<task_id>/items?stage=<stage>&limit=1` 获取当前条。后端分页接口会对候选项做轻量筛选、计数和排序，只对当前页条目构造完整 payload，避免为了返回 1 条数据而 deepcopy 全部候选项。

### 4.1 标注内容

每条数据展示：

- 原图。
- 目标图。
- 生图 Prompt 折叠区。
- MOS 单选项，1 到 5。
- 是否有瑕疵。
- 问题项复选框。

当前前端不再提供单独的主问题、其他问题和备注输入框，但后端数据模型仍兼容 `primary_issue`、`other_issue`、`note` 字段，外部导入时也可写入。

### 4.2 保存与翻页

粗筛支持键盘快速操作：

- 数字键 `1` 到 `5` 选择 MOS。
- `E` 选择“有瑕疵”。
- `Space` 保存当前条并前进到下一条。

点击“下一条”或按空格时会自动保存当前标注；点击“上一条”只切换页面，不强制保存当前条。到达最后一条后继续下一条会保存边界项并提示“已保存”。

### 4.3 多标注人机制

任务可以配置粗筛所需标注人数。每个用户对同一条图片最多保留一条记录；同一用户再次保存会覆盖自己的记录。达到配置人数后，其他用户不能继续为该条新增粗筛记录。

系统会为不同用户名计算不同起始偏移，并优先返回标注人数较少的数据，减少多人同时从同一条开始标注的概率。

### 4.4 聚合规则

粗筛聚合结果保存在 `rough` 字段，个人记录保存在 `rough_annotations`。聚合规则为：

- `mos` 取所有标注人的最低分。
- `has_defect` 只要任一标注人选择有瑕疵即为 true。
- `issues` 合并去重。
- `primary_issue` 取第一条非空主问题。
- `other_issue`、`note` 合并为分号分隔文本。
- `annotator_count` 记录实际标注人数。
- `required_annotator_count` 记录任务要求人数。

只有达到要求人数后，粗筛才算完成。通过规则为：

- 聚合 `mos >= rough.min_mos`。
- 若任务要求 `require_no_defect`，则聚合 `has_defect` 必须为 false。

## 5. 精筛功能

精筛页面位于 `/dataset/rate/<task_id>?stage=fine`。队列只包含已经完成粗筛且粗筛通过的数据。

### 5.1 标注内容

精筛与粗筛使用相同的快速评分界面：

- MOS 单选项。
- 是否有瑕疵。
- 问题项复选框。

如果当前用户尚未保存精筛，前端会使用粗筛聚合结果作为默认值，方便快速确认或微调。

### 5.2 多标注人与聚合

精筛同样支持配置标注人数，个人记录保存在 `fine_annotations`，聚合结果保存在 `fine`。聚合规则与粗筛一致。

精筛保存前会校验该条数据必须已完成粗筛并通过粗筛，否则返回错误“精筛前必须先通过粗筛”。

精筛通过规则为：

- 聚合 `mos >= fine.min_mos`。
- 如果任务启用 `fine.enable_defect`，则聚合 `has_defect` 必须为 false。

## 6. 数据采样

采样页面位于 `/dataset/sample/<task_id>`。采样候选只来自同时满足以下条件的数据：

- 粗筛完成且通过。
- 精筛完成且通过。

### 6.1 分桶统计

页面会调用 `sample-buckets` 接口展示候选分桶。当前分桶统计基于条目已有标签的全部叶子路径，而不只局限于任务选择的纠错路径。每个桶展示：

- 桶名称，例如 `输入图/菜品种类=中餐`。
- 候选数。
- 已采样数。

### 6.2 执行采样

采样支持两种前端操作：

- 按桶输入采样数量。
- 一键全选所有候选。

后端还兼容旧式参数：

- `target_count`。
- `min_per_bucket`。

执行采样时会先清除已有 `sampled` 和 `sample_bucket` 标记，再写入新的采样结果。一个条目可能命中多个统计桶，但最终保存的 `sample_bucket` 使用任务配置的 `selected_label_paths` 组合生成；如果没有选择标签路径，则保存为 `未分组`。

## 7. 标签纠错

标签纠错页面位于 `/dataset/rate/<task_id>?stage=label`。队列只包含已采样数据。

### 7.1 标签草稿

后端会为每条标签纠错数据生成 `label_draft`：

- 优先使用任务配置的 `selected_label_paths`。
- 若未配置，则使用该条原始标签的全部叶子路径。
- 从原始标签中提取当前值。
- 如果已有保存过的纠错标签，则覆盖到草稿上。

### 7.2 选择式纠错

前端不使用自由文本 JSON 输入，而是根据标准标签配置渲染单选条。每个纠错路径对应一组单选项，用户选择后保存为嵌套标签对象。

标签保存要求：

- 必须提供用户名。
- 必须在已采样数据上保存。
- `labels` 必须是对象。

保存成功后写入：

- `label.username`。
- `label.labels`。
- `label.updated_at`。

### 7.3 标签纠错分配

标签阶段支持轻量抢占机制，避免多人同时领取同一条未纠错数据：

- 获取标签队列时，系统会为当前用户保留一个未完成条目。
- 保留信息写入 `label_claim`。
- 保留有效期为 30 分钟。
- 其他用户获取队列时会跳过被别人有效保留的条目。
- 保存标签后会清除该条 `label_claim`。

默认标签队列会隐藏已完成纠错的数据；带历史参数时，只会展示当前用户已完成的数据和当前可做数据，不展示其他用户已完成的数据。

### 7.4 翻页体验

标签纠错同样支持 `Space` 自动保存并前进。与粗筛/精筛不同，标签纠错保存后前端优先在本地队列前进，不立即重新加载全量任务列表和阶段队列，以减少高频翻页时的等待。

## 8. 外部结果导入

任务卡片“导入”会让用户输入一个导入 JSONL 路径，并调用 `/api/tasks/<task_id>/import`。

### 8.1 匹配规则

导入行按以下顺序匹配任务条目：

1. 如果存在 `item_index`，优先按 item index 匹配。
2. 否则按 `src_image` 和 `dst_image` 匹配。
3. 图片路径既支持原始路径，也支持相对 `root_dir` 的路径。

未匹配行会跳过，并在导入摘要中最多返回前 20 条未匹配原因。

### 8.2 可导入字段

支持导入：

- 原始标签：`object_labels`、`labels`、`original_labels`。
- 粗筛记录：`rough_annotations` 或单条 `rough`。
- 精筛记录：`fine_annotations` 或单条 `fine`。
- 采样状态：`sampled`、`sample_bucket`。
- 标签纠错：`corrected_labels` 加 `label_username`，或 `label` 对象。

标注用户名字段支持别名：

- `username`。
- `annotator`。
- `labeler`。

导入完成后返回：

- 总行数。
- 更新行数。
- 跳过行数。
- 未匹配行数。
- 更新了标签的 item 数。
- 更新了阶段记录的 record 数。
- 当前任务 summary。

### 8.3 粗筛 JSONL 批量导入脚本

`scripts/import_annotations_v2_rough_jsonl.py` 用于把外部评分 JSONL 导入为已注册 annotations_v2 任务的粗筛结果。输入每行需要包含：

- `原图`：原图相对路径或相对任务 `root_dir` 的可归一化路径。
- `生成图`：生成图相对路径或相对任务 `root_dir` 的可归一化路径。
- `MOS评分`：字符串或数字形式的 1-5 分。
- `是否有质量问题`：bool，或 `true/false`、`1/0`、`是/否`、`有/无` 字符串。
- `评分人`：写入粗筛记录的用户名。

脚本默认 dry-run，只输出匹配、未匹配、非法行和容量跳过统计，不写入记录：

```bash
python scripts/import_annotations_v2_rough_jsonl.py \
  --jsonl /path/to/ratings.jsonl \
  --task "已注册任务名或task id"
```

确认统计无误后加 `--apply` 写入：

```bash
python scripts/import_annotations_v2_rough_jsonl.py \
  --jsonl /path/to/ratings.jsonl \
  --task "已注册任务名或task id" \
  --apply
```

脚本按 `原图 + 生成图` 与任务 `items.json` 的 `src_image + dst_image` 匹配，同时兼容绝对路径、Windows 反斜杠和相对 `root_dir` 的路径。写入时调用 `AnnotationV2Store.save_rough()`，因此会沿用粗筛人数上限、同用户覆盖、聚合结果和 summary stale 标记等现有规则。达到粗筛人数上限的行会跳过并计入 `capacity_skipped`。

## 9. 导出 JSONL

任务卡片“导出”会下载 `<任务名>_annotations_v2.jsonl`。每行包含：

- `item_index`。
- `src_image`。
- `dst_image`。
- `generation_prompt`。
- `generation_prompt_json_path`。
- `original_labels`。
- `rough` 聚合结果。
- `rough_annotations` 个人记录列表。
- `fine` 聚合结果。
- `fine_annotations` 个人记录列表。
- `sampled`。
- `sample_bucket`。
- `corrected_labels`。
- `label_username`。
- `label_updated_at`。

导出始终覆盖所有任务条目，不只导出完成项。

## 10. 结果可视化

结果可视化页面位于 `/dataset/visualize/<task_id>?stage=<stage>`，支持阶段：

- `rough`：粗筛结果。
- `fine`：精筛结果。
- `sample`：采样结果。
- `label`：标签纠错结果。

### 10.1 阶段候选

不同阶段的可视化候选不同：

- 粗筛：所有条目。
- 精筛：粗筛完成且通过的条目。
- 采样：粗筛、精筛均完成且通过的条目。
- 标签：已采样条目。

### 10.2 展示内容

可视化页每次展示一条图片对，包含：

- 原图和目标图。
- 图片相对路径。
- 生图 Prompt 折叠区。
- 阶段通过/未通过 badge。
- MOS、瑕疵、问题项、标注者、时间。
- 多标注人明细。
- 粗筛/精筛摘要。
- 采样桶。
- 原始标签与纠错标签。

### 10.3 翻页与筛选

可视化页支持：

- 上一条、下一条。
- 输入页码跳转。
- 阶段 tab 切换。
- 筛选抽屉。

筛选维度包括：

- MOS。
- 是否有质量问题。
- 标注者。
- 标签维度和值。

可视化 API 按 `page` 和 `limit` 分页，前端当前固定每页取 1 条，用于保证复查页面不会一次性拉取所有结果。

## 11. 图片加载与缓存

### 11.1 图片接口

图片接口为 `/api/tasks/<task_id>/images/<item_index>/<src|dst>`。

默认优先返回已缓存的预览图：

- 预览图由手动预热缓存或后台缓存任务生成，最长边不超过 1024。
- 若预览缓存命中，接口返回缓存图，并设置 `X-Annotation-Preview-Cache: hit`。
- 若预览缓存未命中，接口不会在请求链路里同步解码、缩放和写入缓存，而是直接返回原文件，并设置 `X-Annotation-Preview-Cache: miss`。
- 缓存 key 包含绝对路径、修改时间、文件大小和最大边长度，原图变化后会使用新 key。
- 缓存查找使用确定的 `<cache_key>.<suffix>` 候选路径，不对缓存目录做通配扫描。

需要原图时可加 `?original=1`，接口会跳过缩放并返回原始文件。

### 11.2 预览缓存目录

预览缓存默认写入任务数据目录下的 `preview_cache`。也可以通过环境变量 `ANNOTATIONS_V2_PREVIEW_CACHE_DIR` 配置到独立目录。配置独立目录时，任务数据目录下不会创建 `preview_cache`。

### 11.3 手动预热缓存

任务卡片提供“缓存图片”按钮，触发后台任务：

- 接口：`POST /api/tasks/<task_id>/preview-cache/jobs`。
- 查询：`GET /api/tasks/<task_id>/preview-cache/jobs/<job_id>`。
- 同一任务已有运行中任务时，会复用当前 job，不重复启动。
- 后端使用 16 个 worker 线程并发处理预览。
- 处理对象为每条数据的原图和目标图。
- 重复图片路径会去重处理，但统计中保留引用数。
- 进度会返回百分比、状态、消息、结果和错误。
- 缓存状态也会写入 `cache_status.json`。

### 11.4 前端预加载

标注页和结果页当前项图片会设置：

- `loading = eager`。
- `decoding = async`。
- `fetchPriority = high`。

标注页会预加载当前项之后 3 条的原图和目标图。预加载图片保存在内存 `Map` 中，最多保留 48 张，超过后移除最早记录。结果页不再通过额外 `/results` 请求预加载后 3 页，避免首屏后立即触发多次全量结果扫描。

## 12. 加载速度与响应需求

以下需求用于约束后续迭代和验收。当前实现已经覆盖其中的任务 summary 快照、单任务标注页加载、阶段分页 payload 优化、图片预热缓存、前端预加载、可视化分页等基础能力；未完全覆盖的指标应作为优化目标。

### 12.1 首屏任务列表

- 首页只应加载任务摘要和必要配置，不应加载完整 `items.json` 图片内容或原图文件。
- 任务列表接口应在本地常见任务规模下保持可感知快速响应；目标为 1 秒内返回任务列表和已有 summary 快照。
- 任务 summary 应只做计数和阶段汇总，不应触发图片读取或预览生成。
- 最新 summary 可通过 `/api/tasks/<task_id>/summary` 异步刷新，不应阻塞首页首屏。

### 12.2 标注页首次进入

- 进入粗筛/精筛/标签页时，应尽快展示第一条可标注数据。
- 标注页应直接加载单任务元信息，不应为了进入任务而等待全部任务列表。
- 当前阶段队列接口不得读取原图二进制，只返回图片 URL、标签、prompt 和记录。
- 当前阶段队列分页接口只应对返回页构造完整 payload。
- 图片加载应优先请求已缓存的 1024 预览图；缓存不存在时可以先返回原图，但不得同步生成预览阻塞响应。

### 12.3 翻页速度

- 下一条操作必须先保存当前结果，保存失败不得前进。
- 粗筛/精筛保存后可以重新加载队列，以保证多人进度准确；但重新加载不应造成明显卡顿。
- 标签纠错高频翻页应优先使用本地队列前进，避免每条都重新拉取全量列表。
- 前端必须继续保留“向后 3 条”预加载策略，降低连续标注时的等待。
- 前端内存预加载上限应保留，避免长时间标注导致浏览器内存持续增长。

### 12.4 图片响应

- 默认图片接口在预览缓存命中时必须返回预览图，最长边不超过 1024。
- 预览缓存未命中时默认图片接口可以返回原图，但不得同步解码、缩放和写入缓存。
- `original=1` 应强制返回原图，即使预览缓存已经命中。
- 预览缓存 key 必须能识别原图修改，避免返回过期图片。
- 手动预热缓存必须异步执行，不阻塞首页任务列表。
- 预热任务必须可查询进度，失败图片应记录到 failures，不应导致整任务中断。

### 12.5 可视化加载

- 结果页面进入时应直接加载单任务元信息，不应为了进入任务而等待全部任务列表。
- 结果页面一次只拉取当前页结果，当前前端固定 `limit=1`。
- 首屏结果请求默认带 `include_filter_options=0`，筛选项通过 `/api/tasks/<task_id>/results/filter-options` 异步加载。
- 无筛选条件时，统一结果接口应直接按 offset/limit 切片，不应逐条执行筛选匹配。
- 结果页首屏不应额外请求后 3 页 `/results` 做预加载。
- 筛选条件改变后应回到第一页。
- 可视化筛选选项可以扫描当前阶段候选数据，但不应读取图片二进制。
- 当任务规模继续增大时，筛选选项计算是潜在瓶颈，需要考虑缓存或增量索引。

### 12.6 任务创建与导入

- 创建任务允许一次性读取 JSONL、标签文件和 prompt 文件，这是离线配置动作，可以比标注翻页慢。
- 创建任务期间不应读取图片二进制，也不应生成预览缓存。
- 外部结果导入应只更新有变化的 `items.json` 或 `records.json`，避免无变化时重复写文件。
- 导入未匹配行应汇总返回，不应因部分未匹配中断整个导入。

### 12.7 并发与文件写入

- JSON 写入必须使用临时文件加原子替换，避免并发写出半截 JSON。
- 同一路径写入必须加锁。
- 标签纠错领取和保存必须在记录文件锁内完成，避免多人抢占同一条。
- 图片缓存预热可并发处理图片，但任务状态更新应保持可读的 JSON。

### 12.8 性能观测

- 所有 Flask 请求会记录总耗时，并在响应头返回 `X-Annotation-Elapsed-Ms`。
- `annotations_v2.performance` logger 会输出 summary 分段：`summary.read_items`、`summary.read_records`、`summary.calculate`、`summary.total`。
- 阶段分页会输出：`items_page.read_records`、`items_page.read_items`、`items_page.filter_sort`、`items_page.payload`、`items_page.total`。
- 结果分页会输出：`results.items_reference`、`results.read_records`、`results.filter_scan`、`results.payload`、`results.total`。
- 结果筛选项会输出：`results_filter_options.items_reference`、`results_filter_options.read_records`、`results_filter_options.calculate`、`results_filter_options.total`。
- 图片接口会输出：`image.path_lookup`、`image.cache_lookup`，其中 `hit=true/false` 可判断是否命中预览缓存。
- 远程部署排查卡顿时，优先按这些 step 区分是任务进度统计、分页候选筛选、records 读取，还是图片缓存未命中。

## 13. API 总览

页面路由：

```text
GET /
GET /dataset/rate/<task_id>
GET /dataset/sample/<task_id>
GET /dataset/visualize/<task_id>
```

任务接口：

```text
GET    /api/tasks
GET    /api/tasks/<task_id>
POST   /api/tasks
PATCH  /api/tasks/<task_id>
DELETE /api/tasks/<task_id>
GET    /api/tasks/<task_id>/summary
```

阶段接口：

```text
GET  /api/tasks/<task_id>/items?stage=rough|fine|label&username=<username>&include_history=0|1
POST /api/tasks/<task_id>/items/<item_index>/rough
POST /api/tasks/<task_id>/items/<item_index>/fine
POST /api/tasks/<task_id>/items/<item_index>/label
```

采样与结果接口：

```text
GET  /api/tasks/<task_id>/sample-buckets
POST /api/tasks/<task_id>/sample
GET  /api/tasks/<task_id>/visualization-results?stage=rough|fine|sample|label&page=0&limit=1
GET  /api/tasks/<task_id>/results?page=0&limit=1&include_filter_options=0
GET  /api/tasks/<task_id>/results/filter-options
POST /api/tasks/<task_id>/results/<item_index>/labels
```

导入导出与图片接口：

```text
POST /api/tasks/<task_id>/import
GET  /api/tasks/<task_id>/download
GET  /api/tasks/<task_id>/images/<item_index>/<src|dst>
POST /api/tasks/<task_id>/preview-cache/jobs
GET  /api/tasks/<task_id>/preview-cache/jobs/<job_id>
```

## 14. 当前限制与注意事项

- 用户体系是浏览器本地用户名，不是严格鉴权。
- 任务删除权限通过用户名判断，适合本地协作环境，不适合开放公网。
- 粗筛/精筛前端当前不展示备注输入，但后端和导入格式兼容备注字段。
- 阶段队列接口当前会返回完整候选队列；超大任务下可能需要分页或增量领取。
- 可视化筛选选项每次由当前阶段候选计算，超大任务下可能需要缓存。
- 文件存储适合本地或小团队使用；如果并发和任务规模继续扩大，需要评估数据库或索引层。
- 采样统计桶使用全部标签叶子路径，最终保存的 `sample_bucket` 使用任务选择的纠错路径，两者口径不同，产品侧需保持理解一致。
