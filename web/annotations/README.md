# 数据打分与标注平台

`web/annotations/` 是本项目的本地 Flask 标注平台，用于把图片对 JSONL 拆分成可分配的子任务，让多人对原图和生成图进行 MOS 打分、标签修正、质检复核、统计分析和结果导出。

## 功能概览

- 创建图片对标注任务：读取包含 `src_image` 和 `dst_image` 的 JSONL。
- 自动拆分子任务：按 `chunk_size` 分片，支持多人领取不同子任务。
- 图片预览：默认把长边压缩到 1024 像素，避免大图拖慢页面。
- 标签加载与刷新：可从已有图片同名 JSON 标签文件加载 AI 标签，并在任务创建后刷新。
- MOS 与标签标注：保存每条图片对的评分、标签、标注人和更新时间。
- 质检复核：对已标注结果进行二次修改，并保留可撤销的质检历史。
- 结果筛选、统计与组合统计：支持按 MOS、标注人、标签值和数值范围过滤。
- 数据导出：支持 JSONL 和 Excel/XLSX。

## 启动

在仓库根目录运行：

```powershell
python -m web.annotations.app
```

默认访问地址：

```text
http://127.0.0.1:5055
```

如需修改端口：

```powershell
$env:ANNOTATIONS_PORT = "5056"
python -m web.annotations.app
```

## 创建任务

在页面中创建任务时通常填写：

- 任务名称：用于页面展示和导出文件名。
- 根目录：图片路径的基准目录。
- JSONL 文件路径：图片对列表。
- 子任务大小：每个子任务包含的图片对数量。
- 标签目录：可选，用于读取原图和生成图对应的已有标签 JSON。

JSONL 每行一个 JSON 对象，至少需要：

```json
{"src_image": "原始图片/a.jpg", "dst_image": "生成图片/a_p1.jpg"}
```

`src_image` 和 `dst_image` 可以是绝对路径，也可以是相对根目录的路径。平台创建任务时会忽略输入行中的 `tags` 字段，并重新从标签目录读取可见标签。

## 标签目录约定

如果创建任务时填写了标签目录，平台会按图片相对路径查找同名 `.json` 文件：

```text
root_dir/原始图片/a.jpg
annotation_dir/原始图片/a.json

root_dir/生成图片/a_p1.jpg
annotation_dir/生成图片/a_p1.json
```

标签 JSON 可以直接是标签对象，也可以放在 `labels` 字段内：

```json
{"labels": {"菜品种类": "中餐"}}
```

平台会把原图标签归入输入图分组，把生成图标签归入输出图分组。任务创建后，如果外部标签文件有更新，可以在页面里刷新任务标签；刷新不会覆盖人工已经保存的标注结果。

## 数据与缓存

任务状态默认保存到：

```text
web/annotations/data/state.json
```

每个任务会在自己的数据目录中保存拆分后的条目、标注结果和预览缓存：

```text
web/annotations/data/tasks/<task_id>/items/
web/annotations/data/tasks/<task_id>/annotations/
web/annotations/data/tasks/<task_id>/preview_cache/
```

图片预览缓存默认位于任务数据目录下。也可以通过环境变量指定统一缓存根目录，每个任务会使用单独的 `<task_id>` 子目录：

```powershell
$env:ANNOTATIONS_PREVIEW_CACHE_DIR = "D:\annotations-preview-cache"
python -m web.annotations.app
```

## 标注流程

1. 创建任务。
2. 输入用户名并领取子任务。
3. 逐条查看原图和生成图，填写 MOS 与标签。
4. 保存当前条目，完成后提交或继续下一条。
5. 如果领取错误，可以放弃当前子任务；平台会释放该子任务并删除该子任务内已保存的标注。

同一个用户再次领取任务时，会优先返回自己尚未完成的子任务。不同用户不会领取到同一个未释放子任务。

## 质检与统计

结果页可以查看已标注结果、筛选结果并做质检修改。质检修改会记录复核人和修改历史，可以撤销最近的有效质检结果，恢复到质检前的标注。

统计接口和页面支持：

- MOS 分布。
- 标注人分布。
- 标签维度分布。
- 2 到 3 个维度的组合统计。
- 按 MOS、标注人、标签枚举值、标签数值范围过滤。

## 导出

导出 JSONL：

```text
/api/tasks/<task_id>/download?format=jsonl
```

导出 Excel：

```text
/api/tasks/<task_id>/download?format=xlsx
```

导出字段包括：

- `item_index`
- `src_image`
- `dst_image`
- `original_tags`
- `tags`
- `mos`
- `username`
- `updated_at`

## 常用 API

```text
GET    /api/tasks
POST   /api/tasks
POST   /api/tasks/jobs
GET    /api/tasks/jobs/<job_id>
DELETE /api/tasks/<task_id>
POST   /api/tasks/<task_id>/labels/refresh
POST   /api/tasks/<task_id>/assign
GET    /api/tasks/<task_id>/subtasks/<subtask_id>
DELETE /api/tasks/<task_id>/subtasks/<subtask_id>
POST   /api/tasks/<task_id>/subtasks/<subtask_id>/annotations
GET    /api/tasks/<task_id>/results
GET    /api/tasks/<task_id>/visualization-results
POST   /api/tasks/<task_id>/results/<item_index>/qc
DELETE /api/tasks/<task_id>/results/<item_index>/qc
GET    /api/tasks/<task_id>/statistics
GET    /api/tasks/<task_id>/download
GET    /api/tasks/<task_id>/images/<item_index>/<src|dst>
```

图片接口默认返回压缩预览图。需要原图时追加：

```text
?original=1
```

## 开发与验证

后端相关测试：

```powershell
python -m pytest tests/test_annotations_app.py
```

前端脚本语法检查：

```powershell
node --check web/annotations/static/app.js
```

如果只改文档，通常不需要启动服务或跑完整测试；如果改动 `app.py`、`static/app.js`、`templates/index.html` 或 `styles.css`，建议至少运行上面的对应检查。
