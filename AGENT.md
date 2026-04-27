# AGENT.md

## 项目定位

本项目是一个面向美食图片生成与质检标注的 Python 工作区，主要包含两条工作线：

- `pipeline/`：模块化的美食图片生成流水线，包含分析、生成、压缩、校验、描述与双图标签等模块。
- `web/annotations/`：本地 Flask 标注平台，用于把图片对 JSONL 拆成子任务、分配标注、保存 MOS/标签并导出结果。

仓库根目录还保留了一批数据整理、抽样、导出、校验脚本。修改前先判断脚本是临时工具还是被测试覆盖的正式工具。

## 目录速览

- `pipeline/config.py`：集中默认配置、模型名、服务名、输入输出目录等。这里目前包含真实 API key，处理时要非常谨慎。
- `pipeline/engine.py`：流水线调度器，负责并发处理输入图片并串联各模块。
- `pipeline/modules/`：流水线阶段模块。常见阶段包括 `AnalysisModule`、`GenerationModule`、`ShortenModule`、`ValidatorModule`、`DescriptionModule`、`TwoImageLabelingModule`。
- `pipeline/utils/`：API 客户端、服务配置、prompt 管理、文件操作和解析工具。
- `scripts/`：数据转换、图片对导出、批量描述/标签、过滤后数据迁移等命令行脚本。
- `tests/`：pytest 测试。优先在这里新增或更新覆盖。
- `web/annotations/app.py`：标注平台后端。
- `web/annotations/static/app.js` 与 `styles.css`：标注平台前端。
- `web/annotations/data/`、`annotations_test_tmp/`、`tests/_scratch_*`：运行或测试产生的数据目录，除非任务明确要求，不要把它们当成源代码来重构。

## 常用命令

在项目根目录运行：

```powershell
python -m pytest tests
```

运行单个测试文件：

```powershell
python -m pytest tests/test_annotations_app.py
```

检查标注平台前端语法：

```powershell
node --check web/annotations/static/app.js
```

启动标注平台：

```powershell
python -m web.annotations.app
```

默认访问地址：

```text
http://127.0.0.1:5055
```

如需换端口，可设置 `ANNOTATIONS_PORT`：

```powershell
$env:ANNOTATIONS_PORT = "5056"
python -m web.annotations.app
```

启动流水线 Web UI：

```powershell
python -m pipeline.web_app
```

默认端口为 `8080`。

运行命令行流水线：

```powershell
python run_pipeline.py -i D:/input -o D:/output -w 3
```

## 数据格式约定

- 图片对 JSONL 通常每行一个 JSON 对象，至少包含 `src_image` 和 `dst_image`。
- `src_image` 表示原始图片，`dst_image` 表示生成图片；路径可以相对输入根目录。
- 标注平台创建任务时会把任务状态写入 `web/annotations/data/state.json`，并把大任务拆成子任务和分片文件。
- 标签 JSON 通常跟随图片相对路径，用同名 `.json` 保存；代码会从原图和生成图两侧读取标签并合并。
- 导出结果支持 JSONL，也支持 Excel/XLSX。

## 编码与中文路径

本项目大量使用中文目录名、中文标签和 Windows 路径。编辑时请遵守：

- Python 文件、JSON、JSONL、Markdown 默认使用 UTF-8。
- 读写 JSON/JSONL 时显式使用 `encoding="utf-8"`，写 JSON 时优先 `ensure_ascii=False`。
- 不要把中文路径手工改成拼音或英文，除非调用方协议同步更新。
- PowerShell 控制台可能显示中文为乱码；不要仅凭控制台乱码判断文件内容损坏，必要时用 Python 或编辑器确认 UTF-8 内容。

## 开发原则

- 优先沿用现有结构：流水线功能放在 `pipeline/modules/`，共享能力放在 `pipeline/utils/`，批处理入口放在 `scripts/`。
- 新增命令行脚本时使用 `argparse`，路径参数用 `pathlib.Path` 处理。
- 涉及 JSONL、标签合并、任务拆分、导出路径保持等行为时，先看现有测试中的期望路径和字段名。
- 对可能调用真实 API 的逻辑，测试中使用 fake 函数、mock client 或小型本地样本，避免默认触发网络请求。
- 并发处理已有 `ThreadPoolExecutor` 模式；修改时注意统计值、异常处理和跳过逻辑不要互相踩踏。
- Web 标注平台的状态文件和任务分片要保持向后兼容，旧 inline task 数据迁移逻辑不可轻易删除。

## 测试策略

- 不要在项目根目录长期生成或保存 pytest 结果目录、缓存目录或临时目录，例如 `pytest-cache-*`、`.pytest_cache/`、`annotations_test_tmp/` 下的临时运行产物等。运行测试若生成了临时文件，应在确认不属于用户数据后及时清理。
- 修改 `scripts/*_jsonl.py`、`move_filtered.py` 或路径解析逻辑时，运行相关 `tests/test_*jsonl.py` 或 `tests/test_move_filtered.py`。
- 修改 `web/annotations/app.py` 时，至少运行 `python -m pytest tests/test_annotations_app.py`。
- 修改 `web/annotations/static/app.js` 时，至少运行 `node --check web/annotations/static/app.js`；涉及交互时再手动打开本地页面验证。
- 修改 `pipeline/modules/` 或 `pipeline/utils/` 时，优先补充不访问真实 API 的单元测试。
- 对真实图片生成、真实 Gemini 调用这类慢且有成本的流程，不要作为默认验证手段；除非用户明确要求并允许网络/API 调用。

## 安全注意事项

- `pipeline/config.py` 和 `pipeline/utils/services.md` 当前含有 API key。不要在回复、日志、提交说明或新文档中复制完整密钥。
- 不要把新的密钥写入源码。更合适的做法是从环境变量、本地私有配置或用户明确指定的文件读取。
- 运行会改动大量图片或标注结果的脚本前，先确认输入输出目录，优先使用小样本或 `--dry-run`（如果脚本提供）。
- 不要随意删除 `web/annotations/data/`、`annotations_test_tmp/`、`tests/_scratch_*` 等数据目录；清理测试产物前先确认它们不是用户正在使用的数据。确认属于本次测试生成的 pytest 缓存、`pytest-cache-*` 或 scratch 临时目录后，应主动清理，不要留在项目下。

## 给后续代理的建议流程

1. 先确认任务影响范围：流水线、脚本、标注平台前端/后端、数据文件，还是文档。
2. 阅读相关测试和现有实现，再做最小必要修改。
3. 对路径、中文字段、JSONL 输出顺序和跳过/恢复逻辑保持保守。
4. 用最窄的相关测试验证；若无法运行，说明原因和剩余风险。
5. 汇报时只描述改动要点和验证结果，不泄露本地密钥或大段数据内容。
