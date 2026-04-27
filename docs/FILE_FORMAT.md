# Pipeline 文件格式说明

本文档描述 Food Photography Pipeline 各阶段产生的文件格式、命名规则及字段含义，用于后续可视化与数据分析。

---

## 1. 整体目录结构

```
input_directory/                    # 输入目录（源图片）
├── image_A.jpg
├── image_B.jpg
└── subdir/
    └── image_C.png

output_directory/                   # 输出目录（保持与输入同样的相对子目录结构）
├── image_A_{seed}_analysis.txt     # 第一阶段：分析文本
├── image_A_p1_{seed}.jpg           # 第二阶段：方案1 生成图片
├── image_A_p1_{seed}.json          # 第三阶段：方案1 完整结果 JSON
├── image_A_p2_{seed}.jpg           # 方案2 生成图片
├── image_A_p2_{seed}.json          # 方案2 完整结果 JSON
├── image_A_p3_{seed}.jpg           # 方案3 ...
├── image_A_p3_{seed}.json
├── image_A_p4_{seed}.jpg           # 方案4（最多4个方案）
├── image_A_p4_{seed}.json
└── subdir/
    ├── image_C_{seed}_analysis.txt
    ├── image_C_p1_{seed}.jpg
    └── image_C_p1_{seed}.json
```

> **说明**：`{seed}` 是由 `config.random_seed` 和图片文件名通过 MD5 哈希确定性生成的 4 位数字。相同种子 + 相同图片名 → 相同 seed 值 → 相同文件名。

---

## 2. 文件命名规则

### 命名模板

| 文件类型 | 命名模板 | 示例 |
|---------|---------|------|
| 分析文本 | `{stem}_{seed}_analysis.txt` | `01XH5r...OkEc_9144_analysis.txt` |
| 生成图片 | `{stem}_p{N}_{seed}.jpg` | `01XH5r...OkEc_p1_9144.jpg` |
| 带主题的生成图片 | `{stem}_p{N}_{theme}_{seed}.jpg` | `01XH5r...OkEc_p1_烟火铜锅_9144.jpg` |
| 结果 JSON | `{stem}_p{N}_{seed}.json` | `01XH5r...OkEc_p1_9144.json` |

### 各部分含义

| 组成部分 | 说明 |
|---------|------|
| `{stem}` | 原始输入图片的文件名（去掉扩展名） |
| `{N}` | 方案序号，从 1 开始（最多 4） |
| `{theme}` | 方案主题名（从分析结果中提取），为空时省略 |
| `{seed}` | 4 位确定性种子码，由 `MD5(random_seed + "_" + stem)` 前 8 位取模 10000 |

> **注意**：文件名中的非法字符 `\ / * ? : " < > |` 会被自动移除。

---

## 3. 第一阶段输出：Analysis Text（`.txt`）

### 用途
保存 AI 视觉分析的完整原始文本（包含 2-4 个拍摄方案），用于跳过/恢复判断。

### 格式
纯文本 Markdown 格式，包含多个以 `### 方案N：` 开头的方案段落。

### 样例（节选）

```markdown
### 方案1：[平视微仰特写｜烟火铜锅，匠心质感]
此方案旨在抛弃杂乱的桌面，将视觉焦点完全集中在景泰蓝铜锅的精美工艺和沸腾的烟火气上...
*   **美食布局**：将最精致的那个景泰蓝铜锅移至画面中右侧...
*   **构图**：三分法构图。左侧留白让给升腾的烟气...
*   **相机机位与角度**：超低视平线，相机几乎贴着桌面，微微仰拍（约0-10度）...
*   **焦段**：中长焦（85mm 或 105mm）...
*   **打光**：**侧逆光（关键）**。主光源放在铜锅斜后方45度...
*   **色调调整**：电影级暖调（Cinematic Warm）...

### 方案2：[90度俯拍平铺｜色彩盛宴，几何阵列]
利用桌面的黑色底色作为极佳的负空间...

### 方案3：[45度食客第一视角｜垂涎欲滴，蘸料诱惑]
...

### 方案4：[对角线局部切割｜食材肌理，质感碰撞]
...
```

---

## 4. 第二阶段输出：Generated Image（`.jpg`）

### 用途
AI 根据分析方案重新生成的美食摄影图片。每个方案对应一张。

### 格式
JPEG 图片，分辨率和尺寸由 AI 图像生成 API 决定，通常约 600-900 KB。

---

## 5. 第三阶段输出：Result JSON（`.json`）

### 用途
每个方案的完整数据记录，是管线最终交付物。与对应的生成图片同名同目录。

### Schema 定义

```json
{
    "theme":                 "string  | 方案主题名称，如 '烟火铜锅'",
    "original_image_path":   "string  | 原始输入图片的绝对路径",
    "generated_image_path":  "string? | 生成图片的绝对路径（失败时为 null）",
    "original_plan":         "string  | 第一阶段分析出的完整方案文本",
    "short_plan":            "string? | 第三阶段缩短后的精简摘要（≤120字）",
    "analysis_prompt_used":  "string  | 分析阶段使用的完整 prompt",
    "shorten_prompt_used":   "string? | 缩短阶段使用的完整 prompt",
    "mode":                  "string  | 缩短模式：'A'/'B'/'C'",
    "camera_movement":       "object? | 提取的相机运动参数",
    "status":                "string  | 状态：'pending'/'analyzed'/'generated'/'success'/'failed'",
    "error_message":         "string? | 错误信息（成功时为 null）"
}
```

### 字段详解

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `theme` | string | ✅ | 从分析文本中提取的方案主题标题 |
| `original_image_path` | string | ✅ | 原始输入图片的绝对路径 |
| `generated_image_path` | string \| null | ❌ | 成功时为生成图片的绝对路径 |
| `original_plan` | string | ✅ | 分析模块输出的完整方案描述（可能数百字） |
| `short_plan` | string \| null | ❌ | 缩短模块输出的精简摘要，XML `<scheme>` 标签包裹 |
| `analysis_prompt_used` | string | ✅ | 分析阶段发送给 AI 的完整 prompt 文本 |
| `shorten_prompt_used` | string \| null | ❌ | 缩短阶段的完整 prompt（含原始方案拼接） |
| `mode` | string | ✅ | 缩短模式。A=纯文本，B=纯文本，C=传入生成图对比 |
| `camera_movement` | object \| null | ❌ | 从方案文本中正则提取的相机参数 |
| `status` | string | ✅ | 管线执行状态（见下方状态机） |
| `error_message` | string \| null | ❌ | 失败时的错误详情 |

### `camera_movement` 对象结构

```json
{
    "pitch":    "string? | 俯仰角描述",
    "yaw":      "string? | 偏航角描述",
    "distance": "string? | 相机距离描述",
    "zoom":     "string? | 焦段描述"
}
```

### `status` 状态流转

```
pending → analyzed → generated → success
              │            │
              └─ failed ←──┘
```

| 状态 | 含义 |
|------|------|
| `pending` | 初始状态 |
| `analyzed` | 分析阶段完成，已获得方案文本 |
| `generated` | 图片生成成功 |
| `success` | 缩短完成，JSON 已保存（最终态） |
| `failed` | 任一阶段出错（终态） |

### 样例

```json
{
    "theme": "",
    "original_image_path": "D:\\test\\food_test\\01XH5r...OkEc.jpg",
    "generated_image_path": "D:\\test\\food_test_output\\01XH5r...OkEc_p1_9144.jpg",
    "original_plan": "### 方案1：[平视微仰特写｜烟火铜锅，匠心质感]\n此方案旨在抛弃杂乱的桌面...\n*   **美食布局**：将最精致的那个景泰蓝铜锅移至画面中右侧...",
    "short_plan": "<scheme>\n    ### 方案1：[平视特写｜烟火铜锅]\n    - **摆盘建议**：清空杂物，右置景泰蓝铜锅...\n    - **构图**：三分法，铜锅居右...\n    - **机位**：贴近桌面，拉近距离。\n    - **焦段**：3X长焦。\n    - **角度**：平视微仰约10度。\n    - **打光**：侧逆光打透烟气...\n    - **色调**：压暗背景，电影级暖调...\n</scheme>",
    "analysis_prompt_used": "# Role\n你是一个专业的美食摄影师...",
    "shorten_prompt_used": "你将看到一张原图...原始方案如下：...",
    "mode": "C",
    "camera_movement": {
        "zoom": "中长焦（85mm 或 105mm）..."
    },
    "status": "success",
    "error_message": null
}
```

---

## 6. `short_plan` 内部格式

`short_plan` 字段的值遵循 XML 结构（经 `clean_xml_markdown` 处理后可能不含外层标签）：

```xml
<scheme>
    ### 方案N：[景别角度|主题]
    - **摆盘建议**：[具体调整建议]
    - **构图**：[构图方法，主体位置和占比]
    - **机位**：[相机机位调整]
    - **焦段**：[1X / 3X 和 XX焦段]
    - **角度**：[拍摄俯仰角XX度]
    - **打光**：[打光调整]
    - **色调**：[色调风格调整]
</scheme>
```

---

## 7. 跳过/恢复机制与文件关系

管线支持断点续跑。以下是各阶段通过检测已有文件来决定跳过还是重新生成的逻辑：

| 阶段 | 检测文件 | 检测条件 | 行为 |
|------|---------|---------|------|
| Analysis | `{stem}_{seed}_analysis.txt` | 文件存在且非空 | 跳过分析，从 txt 恢复 |
| Generation | `{stem}_p{N}_{seed}.jpg` | 文件存在 | 跳过该方案图片生成 |
| Shortener | `{stem}_p{N}_{seed}.json` | 文件存在且 `short_plan` 非空 | 跳过该方案缩短 |

> 更换 `random_seed`（`--seed` 参数）会生成不同的 `{seed}` 值，从而产生全新的文件名，不会与旧文件冲突。

---

## 8. 一张输入图的完整输出示例

输入图片：`01XH5r...OkEc.jpg`（seed = `9144`，含 4 个分析方案）

```
output_directory/
├── 01XH5r...OkEc_9144_analysis.txt    # 分析文本（含4个方案）
├── 01XH5r...OkEc_p1_9144.jpg          # 方案1 生成图
├── 01XH5r...OkEc_p1_9144.json         # 方案1 结果
├── 01XH5r...OkEc_p2_9144.jpg          # 方案2 生成图
├── 01XH5r...OkEc_p2_9144.json         # 方案2 结果
├── 01XH5r...OkEc_p3_9144.jpg          # 方案3 生成图
├── 01XH5r...OkEc_p3_9144.json         # 方案3 结果
├── 01XH5r...OkEc_p4_9144.jpg          # 方案4 生成图
└── 01XH5r...OkEc_p4_9144.json         # 方案4 结果
```

**一张图共产生**：1 个 analysis.txt + N 张 .jpg + N 个 .json（N = 方案数，最多 4）
