# 数据打分 & 标注平台

## 启动

在仓库根目录运行：

```powershell
python -m web.annotations.app
```

默认访问地址：

```text
http://127.0.0.1:5055
```

可以通过环境变量修改端口：

```powershell
$env:ANNOTATIONS_PORT="5056"
python -m web.annotations.app
```

## 数据与缓存

任务状态默认保存到：

```text
web/annotations/data/state.json
```

图片预览缓存默认保存到：

```text
web/annotations/data/tasks/<task_id>/preview_cache
```

可以通过环境变量指定图片预览缓存根目录，每个任务会使用单独的 `<task_id>` 子目录：

```powershell
$env:ANNOTATIONS_PREVIEW_CACHE_DIR="D:\annotations-preview-cache"
python -m web.annotations.app
```

新建任务时填写根目录和 jsonl 文件路径。jsonl 每行需要包含 `src_image` 和 `dst_image`，`tags` 可选。
