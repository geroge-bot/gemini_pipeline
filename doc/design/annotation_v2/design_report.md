# Annotation V2 Design Report

## 1. 背景与目标

`docs/annotation_v2.md` 描述的新流程要求把当前 `web/annotations` 的单轮 MOS/标签标注，升级为完整的数据标注流水线：

1. 数据与标签导入。
2. 第一轮粗筛：创建人配置问题项，标注者评价 MOS、是否有瑕疵，并记录问题。
3. 第二轮精筛：仅对粗筛合格数据继续评价 MOS，瑕疵项可选。
4. 基于数据分布进行采样。
5. 对采样数据进行标签纠错，纠错维度在创建任务时选定。

新网站实现到 `web/annotations_v2/`，与旧版 `web/annotations/` 并存。v2 先完成完整闭环和可验证 API，再逐步吸收旧版中的高级能力，例如预览缓存、Issue 协作和复杂统计。

## 2. 设计原则

- **独立演进**：v2 使用独立包、独立状态文件和独立端口，避免影响当前旧版标注站点。
- **阶段化数据模型**：同一条图片对不再只有一个最终 annotation，而是拥有 `rough`、`fine`、`label` 三类阶段记录。
- **规则透明**：粗筛、精筛和采样的准入规则都保存在 task 配置中，接口和前端展示同一份状态。
- **文件优先**：沿用本项目本地 Flask + JSON 文件的部署方式，不引入数据库。
- **可导出**：最终结果以 JSONL 导出，包含源数据、各阶段评分、问题、采样状态和纠错后标签。

## 3. 范围

### 本期包含

- 创建 v2 任务，导入图片对 JSONL。
- 可选导入外部标签目录中的同名 JSON 标签。
- 创建任务时配置：
  - 粗筛主问题和其他问题选项。
  - 粗筛通过阈值，默认 `MOS >= 4` 且无瑕疵。
  - 精筛通过阈值，默认 `MOS >= 4`。
  - 精筛是否启用瑕疵字段。
  - 本次要纠错的标签路径。
- 阶段看板展示数据总数、粗筛完成/通过、精筛完成/通过、采样数、标签完成数。
- 粗筛、精筛和标签纠错页面。
- 基于标签分布的采样：
  - 在精筛通过数据中采样。
  - 优先按选定标签路径形成分布桶。
  - 每个桶至少取指定数量，剩余名额按桶顺序补齐。
- 导出 JSONL。

### 本期不包含

- 多用户子任务抢占分配。
- 旧版的质检历史撤销、Issue 标注区域截图、Excel 导出和图片预览缓存。
- 复杂可视化图表。

这些能力在 v2 模型稳定后可以迁移。

## 4. 信息架构与用户流程

### 任务创建

创建人填写任务名称、根目录、JSONL 路径、标签目录、筛选阈值、问题选项和标签纠错路径。后端读取 JSONL，要求每行至少包含 `src_image` 与 `dst_image`。如提供标签目录，后端根据图片相对路径读取同名 `.json`，合并为 `labels`。

### 粗筛

标注者逐条查看原图、目标图和已有标签，填写：

- MOS：1 到 5。
- 是否有瑕疵。
- 主问题。
- 多个问题项。
- 备注。

粗筛通过规则默认是 `mos >= 4 && has_defect == false`。

### 精筛

精筛队列只包含粗筛通过数据。标注者填写第二轮 MOS；如果任务启用了精筛瑕疵字段，也可以再次记录是否有瑕疵和问题。精筛通过规则默认是 `mos >= 4`，若启用瑕疵字段则同时要求无瑕疵。

### 采样

采样从精筛通过数据中选择。系统按创建任务时选定的标签纠错路径形成分布桶，例如 `输入图/菜品种类` 或 `输出图/美学评分`。如果没有选定路径或某条数据没有对应标签，则归入 `未分组`。采样结果写入任务状态，前端显示各桶候选数和采中数。

### 标签纠错

标签纠错队列只包含采样数据。页面展示选定标签路径的当前值，标注者可以修改后保存。导出时优先输出纠错后的 `corrected_labels`，并保留原始导入标签。

## 5. 后端架构

### 文件结构

```text
web/annotations_v2/
  __init__.py
  app.py
  data/state.json
  data/tasks/<task_id>/items.json
  data/tasks/<task_id>/records.json
  templates/index.html
  static/app.js
  static/styles.css
```

### 核心类

`AnnotationV2Store` 是唯一持久化入口，负责：

- 读写全局 state。
- 创建任务。
- 读取任务条目和阶段记录。
- 保存粗筛、精筛、标签纠错记录。
- 计算阶段候选队列、汇总指标和采样结果。
- 导出 JSONL。

Flask 路由只负责参数校验、调用 store、返回 JSON 或页面。

### 状态模型

`state.json` 保存任务摘要和规则配置：

```json
{
  "tasks": [
    {
      "id": "uuid",
      "name": "food",
      "root_dir": "/data/images",
      "jsonl_path": "/data/data.jsonl",
      "label_dir": "/data/labels",
      "data_dir": "web/annotations_v2/data/tasks/uuid",
      "rough": {
        "min_mos": 4,
        "primary_issue": "主体问题",
        "issue_options": ["主体问题", "构图问题"],
        "require_no_defect": true
      },
      "fine": {
        "min_mos": 4,
        "enable_defect": false
      },
      "selected_label_paths": [["输入图", "菜品种类"]]
    }
  ]
}
```

`items.json` 保存导入数据：

```json
[
  {
    "item_index": 0,
    "src_image": "src/a.jpg",
    "dst_image": "dst/a.jpg",
    "labels": {
      "输入图": {"菜品种类": "中餐"},
      "输出图": {"美学评分": 4}
    }
  }
]
```

`records.json` 保存阶段记录：

```json
{
  "0": {
    "rough": {
      "username": "alice",
      "mos": 4,
      "has_defect": false,
      "primary_issue": "",
      "issues": [],
      "note": "",
      "updated_at": 1780713600.0
    },
    "fine": {
      "username": "bob",
      "mos": 5,
      "has_defect": false,
      "issues": [],
      "note": "",
      "updated_at": 1780713700.0
    },
    "label": {
      "username": "carol",
      "labels": {"输入图": {"菜品种类": "西餐"}},
      "updated_at": 1780713800.0
    },
    "sampled": true,
    "sample_bucket": "输入图/菜品种类=中餐"
  }
}
```

## 6. API 设计

```text
GET    /
GET    /api/tasks
POST   /api/tasks
GET    /api/tasks/<task_id>/summary
GET    /api/tasks/<task_id>/items?stage=rough|fine|label
POST   /api/tasks/<task_id>/items/<item_index>/rough
POST   /api/tasks/<task_id>/items/<item_index>/fine
POST   /api/tasks/<task_id>/items/<item_index>/label
POST   /api/tasks/<task_id>/sample
GET    /api/tasks/<task_id>/download
GET    /api/tasks/<task_id>/images/<item_index>/<src|dst>
```

### 队列规则

- `stage=rough`：返回所有数据。
- `stage=fine`：返回粗筛通过数据。
- `stage=label`：返回已采样数据。

## 7. 前端设计

v2 首页是工作台，不做营销页。

页面区域：

- 顶栏：当前用户、刷新、回到任务列表。
- 任务创建栏：紧凑表单，支持路径和规则配置。
- 任务列表：显示阶段进度和操作入口。
- 阶段工作区：
  - 左侧原图和目标图。
  - 中间已有标签和阶段记录。
  - 右侧当前阶段表单。
  - 底部上一条、下一条、保存。
- 采样面板：输入采样目标数和每桶最小数量，执行后展示桶分布。

视觉风格应偏工作台：高信息密度、浅色背景、少量强调色、8px 内圆角、清晰表格和按钮，不使用大 hero 或装饰性渐变。

## 8. 错误处理

- JSONL 缺少 `src_image` 或 `dst_image` 时返回 400，并包含行号。
- MOS 必须是 1 到 5。
- 精筛保存前必须存在通过的粗筛记录。
- 标签纠错保存前必须是已采样数据。
- 图片不存在返回 404。

## 9. 测试策略

后端测试覆盖：

- 创建任务并读取标签目录。
- 粗筛通过规则。
- 精筛队列只包含粗筛通过数据。
- 采样只从精筛通过数据中选择，并输出分布。
- 标签纠错只允许采样数据。
- 下载 JSONL 包含所有阶段字段。

前端验证：

- `node --check web/annotations_v2/static/app.js`。
- 启动 Flask 应用后用浏览器打开首页，检查任务创建表单、阶段工作区和控制台错误。

## 10. 迁移与后续演进

- 确认 v2 阶段模型稳定后，可迁移旧版预览缓存和 Excel 导出。
- 多人协作可在 `stage_records` 外增加 `assignments`，按阶段而不是按全任务分片。
- 复杂统计可直接基于 `summary` 和 `records.json` 扩展，不需要改变导入模型。
