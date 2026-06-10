# Gemini Pipeline

这是一个面向美食图片生成、整理、标注和质检的 Python 工作区。项目主要包含两条工作线：

- `pipeline/`：模块化的美食图片生成流水线，负责图片分析、拍摄方案生成、图片生成、方案压缩、校验和双图标签等流程。
- `web/annotations/`：本地 Flask 标注平台，负责把图片对 JSONL 拆成子任务，支持多人 MOS 打分、标签修正、质检复核、统计和导出。

仓库根目录还包含一批数据整理、抽样、导出、校验和批处理脚本，用于把流水线输出转成后续标注和分析可以消费的格式。

## 目录结构

```text
.
├── pipeline/                 # 图片生成流水线核心代码
│   ├── modules/              # 分析、生成、压缩、校验、描述、双图标签等模块
│   ├── utils/                # API 客户端、服务配置、prompt、文件工具
│   ├── web/                  # 流水线 Web UI 静态页面
│   └── web_app.py            # 流水线 Web UI 后端入口
├── scripts/                  # 数据转换、导出、标注、迁移等命令行工具
├── web/annotations/          # 本地数据打分与标注平台
├── tests/                    # pytest 测试
├── docs/                     # 数据格式和业务说明文档
├── run_pipeline.py           # 命令行运行图片生成流水线
├── run_description_sample.py # 描述阶段示例入口
├── run_shortener_batch.py    # 批量压缩/整理结果入口
└── AGENT.md                  # 面向自动化协作代理的项目约定
```

## 环境准备

建议使用 Python 3.11+。本项目代码依赖的常见包包括：

- `flask`
- `pillow`
- `openpyxl`
- `pytest`

如果本地环境缺包，可以按报错补装，例如：

```powershell
python -m pip install flask pillow openpyxl pytest
```

项目中包含真实服务配置和历史 API key。不要把新的密钥写入源码，也不要在日志、提交说明或文档中复制完整密钥。更推荐通过环境变量或本地私有配置管理新增凭据。

## 运行图片生成流水线

命令行入口：

```powershell
python run_pipeline.py -i D:\input_images -o D:\output_images -w 3
```

常用参数：

- `-i, --input`：输入图片目录。
- `-o, --output`：输出目录。
- `-w, --workers`：并发 worker 数。
- `--mode`：压缩模式，支持 `A`、`B`、`C`。
- `--seed`：确定性文件命名种子。
- `--model-analysis`：分析阶段模型。
- `--model-generation`：图片生成阶段模型。
- `--model-shorten`：方案压缩阶段模型。

流水线默认会按顺序串联：

1. `AnalysisModule`：分析原始图片并产出多个拍摄/生成方案。
2. `GenerationModule`：根据方案生成图片。
3. `ShortenModule`：压缩生成方案文本。
4. `ValidatorModule`：按配置执行结果校验。

Prompt 默认从 `pipeline/utils/prompts.md` 读取，服务配置从 `pipeline/config.py` 和 `pipeline/utils/services.md` 读取。

## 启动流水线 Web UI

```powershell
python -m pipeline.web_app
```

默认端口为：

```text
http://127.0.0.1:8080
```

该 UI 用于在本地启动、查看或辅助操作流水线任务。涉及真实图片生成或真实模型调用时，请先确认输入输出目录和调用成本。

## 启动标注平台

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

标注平台的详细说明见 [web/annotations/README.md](web/annotations/README.md)。

Annotation V2 的启动、环境变量、Nginx 和预览缓存配置见 [web/annotations_v2/README.md](web/annotations_v2/README.md)。

## 图片对 JSONL 格式

标注平台和部分脚本使用图片对 JSONL。每行一个 JSON 对象，至少包含：

```json
{"src_image": "原始图片/a.jpg", "dst_image": "生成图片/a_p1.jpg"}
```

约定：

- `src_image` 表示原始图片。
- `dst_image` 表示生成图片。
- 路径可以是绝对路径，也可以是相对任务根目录的路径。
- 已有标签通常保存在同名 `.json` 文件中，由标注平台按图片路径从标签目录读取。

如果需要从整理后的图片目录导出图片对 JSONL，可以参考：

```powershell
python scripts/export_pairs_jsonl.py --input_dir D:\dataset --output_jsonl D:\pairs.jsonl
```

更多流水线输出文件格式见 [docs/FILE_FORMAT.md](docs/FILE_FORMAT.md)。

## 常用脚本

```powershell
python scripts/export_pairs_jsonl.py --input_dir D:\dataset --output_jsonl D:\pairs.jsonl
python scripts/describe_pairs_jsonl.py --help
python scripts/label_pairs_jsonl.py --help
python scripts/describe_and_label_pairs_jsonl.py --help
python scripts/move_filtered.py --help
```

这些脚本多用于批量处理真实图片和 JSONL。运行前请先用小样本确认输入输出目录，避免批量改动历史数据。

## 测试与检查

运行全部测试：

```powershell
python -m pytest tests
```

只运行标注平台测试：

```powershell
python -m pytest tests/test_annotations_app.py
```

检查标注平台前端脚本语法：

```powershell
node --check web/annotations/static/app.js
```

如果改动的是 `scripts/*_jsonl.py` 或路径迁移逻辑，优先运行对应的 `tests/test_*jsonl.py`、`tests/test_move_filtered.py`。

## 数据目录与注意事项

以下目录通常包含运行数据、缓存或测试产物，不应随意删除或重构：

- `web/annotations/data/`
- `annotations_test_tmp/`
- `tests/_scratch_*`
- 各级 `__pycache__/`

标注平台默认把状态保存到：

```text
web/annotations/data/state.json
```

创建任务后，条目、标注结果和预览缓存会进入：

```text
web/annotations/data/tasks/<task_id>/
```

如果只是开发代码或文档，尽量不要改动这些数据目录。确实需要清理时，先确认不是正在使用的用户数据。

## 开发约定

- Python、JSON、JSONL、Markdown 默认使用 UTF-8。
- 读写 JSON/JSONL 时显式指定 `encoding="utf-8"`，写 JSON 优先使用 `ensure_ascii=False`。
- 新增命令行脚本时优先使用 `argparse` 和 `pathlib.Path`。
- 涉及真实模型/API 调用的测试应使用 fake client 或小样本，避免默认触发网络请求和成本。
- 修改共享路径解析、标签合并、导出格式、任务拆分逻辑时，先看现有测试中的字段名和路径期望。
