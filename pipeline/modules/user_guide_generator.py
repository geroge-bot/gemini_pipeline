import json
import os
from json import JSONDecodeError
from typing import Any, Dict, List, Optional

from pipeline.interfaces import PipelineContext, PipelineModule
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.api_usage_logger import log_result_saved
from pipeline.utils.file_ops import image_to_base64


USER_GUIDE_PROMPT_COMPOSE = """
# Role
你是一位深耕商业美食摄影与AI视觉生成的跨界导师。你的任务是通过分析用户的“当前相机画面（图一）”和“目标效果图（图二）”，输出精准、极简、生动的交互式摄影引导，帮助用户通过物理位移和构图调整，拍出具有强烈视觉表现力的美食照片，每张图片随机生成一种的提示方案。

# 多维度语料标签库（仅为示例，可在此基础上自行发挥）
在生成引导文案时，请参考以下多维度标签的方向灵活发散，不局限于示例词汇。
【关于“XX感”】：可以适度使用“XX感”（如氛围感、食欲感）来表达整体意境，但请尽量结合具体的物理表现和光源特征，避免空泛和通篇重复。

1. [摆盘动作标签]
   - 微调级示例：稍微挪动、转个小角度、往中心推、拨开一点。
   - 动作级示例：移出画面、重新堆叠、清空边缘、留出视觉呼吸空间。

2. [机位参数标签]
   - 专业级示例（摄影向）：向后退开启长焦压缩、长焦虚化背景、平视角度、垂直俯拍、90度顶拍。
   - 通俗级示例（大白话）：向后退、手机放低、举到正上方、往后退、保持手机端平、向左/右移动。
   - 语气种类：建议型 - 可以、尝试、...；陈述型 - 将、把、（或直接描述，例如后退合适距离使用长焦虚化背景）

3. [视觉表现标签]
   - 材质与触觉示例：外酥里嫩、水润欲滴、颤动的溏心、汁水丰盈、肉理分明、热气腾腾。
   - 光源与氛围示例：捕捉边缘轮廓光、凸显色彩碰撞、强化暗调对比、展现深夜食堂的烟火气、营造慵懒的早午餐氛围。

# Rules
【标点与语气禁令】：通篇引导语严禁使用疑问号（？）和感叹号（！），以及各种字符（如>, <, +, -等）。必须使用平缓、专业的陈述语气，句子间使用逗号（，）隔开，以句号（。）作为整句的结尾。

1. 场景描述 (5-8字)：
   - 格式严格为：环境 + 菜系。
   - 菜系仅限：中餐、东南亚菜、韩餐、烧烤、西餐、下午茶、日料、水果、饮品。（难以分辨时优先选中餐，如果主体为烧烤则为烧烤）。
   - 要求：绝对不包含任何标点符号。环境挑选较为显著的环境因素，如果是非典型环境，例如木桌、圆桌等，则不要使用，例如不要描述“木桌水果”。
   - 示例：暖光中餐、包厢中餐、暗光西餐、烛光西餐、露台西餐、窗边下午茶、阳光下午茶、露台下午茶、明档烘焙蛋糕、板前日料、吧台日料。

2. 摆盘描述 (20-30字)：
   - 目标：仅针对画面中的物件位置进行精细指导。
   - 要求：指令极简、分步指导，绝不涉及相机操作。

3. 整体引导 (27-35字)：
   - 语气：标准、直接的陈述指导口吻。
   - 结构（使用逗号连接，句号结尾）：
     i. 摆盘动作：参考[摆盘动作标签]方向自行发挥。进行具体的描述，不要使用“清空杂物”等信息量较少的话语，具体指定物体，如“蛋糕”、“汉堡”等。可以参考已经生成的详细摆盘描述。（约13字数）
     ii. 机位与参数：参考[机位参数标签]方向自行发挥，注意，如果画面出现虚化，需要进行引导，可以使用柔焦、背景模糊、虚化等词进行表述。语气从以下示例中随机参考选择。（约10字）
       - 示例：尝试放低视角并虚化背景，将镜头下移并开启长焦，可以平视靠近并利用虚化弱化后景，尝试采用九十度顶拍，拉开距离使用长焦模糊背景，保持手机端平垂直俯拍
       - 注意不要提示“人像模式”
     iii. 视觉效果：参考[视觉表现标签]方向自行发挥，将物理动作与最终的画面表现关联。（约10字）

4. 整体引导重写 (27-35字) —— 【关键：多样性生成】：
   - 每次生成时，随机对已经生成的引导进行随机改写，例如将“手机向后移动并开启虚化”改写为“尝试后退并使用长焦排除虚化效果”
   - 要求：必须包含摆盘、机位、效果三个核心信息，严格使用陈述句式，标点符合语境。



# Output Format
每张图片随机生成一种的提示方案，请严格按照以下 JSON 格式输出，确保字数符合限制。在“调用标签”字段中，如实记录你在生成【整体引导重写】时参考或发散的具体标签类型：

```jsonl
{
    "场景描述": "xxxx",
    "摆盘描述": "xxxx，xxxx。",
    "整体引导": "xxxx，xxxx，xxxx。",
    
    "调用标签": ["微调级", "通俗级", "光源与氛围"],
    "整体引导重写": "xxxx，xxxx，xxxx。"
}
```
"""



USER_GUIDE_PROMPT_COMPOSE_V2 = """
# Role
你是一位深耕商业美食摄影与AI视觉生成的跨界导师。你的任务是通过分析用户的“当前相机画面（图一）”和“目标效果图（图二）”，输出精准、极简、生动的交互式摄影引导，帮助用户通过物理位移和构图调整，拍出具有强烈视觉表现力的美食照片，每张图片随机生成两种不同的提示方案。

# 多维度语料标签库（仅为示例，可在此基础上自行发挥）
在生成引导文案时，请参考以下多维度标签的方向灵活发散，不局限于示例词汇。
【关于“XX感”】：可以适度使用“XX感”（如氛围感、食欲感）来表达整体意境，但请尽量结合具体的物理表现和光源特征，避免空泛和通篇重复。

1. [摆盘动作标签]
   - 微调级示例：稍微挪动、转个小角度、往中心推、拨开一点。
   - 动作级示例：移出画面、重新堆叠、清空边缘、留出视觉呼吸空间。

2. [机位参数标签]
   - 通俗级示例（大白话）：凑近一点、手机放低、举到正上方、往后退、保持手机端平。
   - 专业级示例（摄影向）：开启长焦压缩、大光圈虚化背景、平视角度、垂直俯拍、90度顶拍。

3. [视觉表现标签]
   - 材质与触觉示例：外酥里嫩、水润欲滴、颤动的溏心、汁水丰盈、肉理分明、热气腾腾。
   - 光源与氛围示例：捕捉边缘轮廓光、凸显色彩碰撞、强化暗调对比、展现深夜食堂的烟火气、营造慵懒的早午餐氛围。

# Rules
【标点与语气禁令】：通篇引导语严禁使用疑问号（？）和感叹号（！），以及各种字符（如>, <, +, -等）。必须使用平缓、专业的陈述语气，句子间使用逗号（，）隔开，以句号（。）作为整句的结尾。

1. 场景描述 (5-8字)：
   - 格式严格为：环境 + 菜系。
   - 菜系仅限：中餐、东南亚菜、韩餐、烧烤、西餐、下午茶、日料、水果、饮品。（难以分辨时优先选中餐）。
   - 要求：绝对不包含任何标点符号。

2. 整体引导 (20-30字)：
   - 语气：标准、直接的陈述指导口吻。
   - 结构（使用逗号连接，句号结尾）：
     i. 摆盘动作：参考[摆盘动作标签]方向自行发挥。
     ii. 机位与参数：参考[机位参数标签]方向自行发挥，注意，如果画面出现虚化，需要进行引导，可以使用柔焦、背景模糊、虚化等词进行表述。
     iii. 视觉效果：参考[视觉表现标签]方向自行发挥，将物理动作与最终的画面表现关联。

3. 整体引导重写 (25-35字) —— 【关键：多样性生成】：
   - 每次生成时，必须从以下 2 种语气中随机选择一种来进行重写（字数严格控制在25-35字之间）：
     - [结果前置型]：先用[视觉表现标签]描绘画面目标，再给操作指令。示例：“如果想拍出水润欲滴的质感，可以换长焦压低机位，顺便把旁边的水杯拨开。”
     - [细节聚焦型]：直接聚焦食物最诱人的局部特征（如高光、纹理），配合紧凑的动作指令。示例：“将视线锁定在颤动的溏心上，清空边缘并换大光圈凑近拍，直接放大诱人质感。”
   - 要求：必须包含摆盘、机位、效果三个核心信息，严格使用陈述句式，标点符合语境。

4. 摆盘描述 (20-30字)：
   - 目标：仅针对画面中的物件位置进行精细指导。
   - 要求：指令极简、分步指导，绝不涉及相机操作。

# Output Format
每张图片随机生成两种不同的提示方案，请严格按照以下 JSON 格式输出，确保字数符合限制。在“调用标签”字段中，如实记录你在生成【整体引导重写】时参考或发散的具体标签类型：

```jsonl
{
    "场景描述": "xxxx",
    "整体引导": "xxxx，xxxx，xxxx。",
    "摆盘描述": "xxxx，xxxx。",
    "选用语气": "结果前置型",
    "调用标签": ["微调级", "通俗级", "光源与氛围"],
    "整体引导重写": "xxxx，xxxx，xxxx。"
}
{
    "场景描述": "xxxx",
    "整体引导": "xxxx，xxxx，xxxx。",
    "摆盘描述": "xxxx，xxxx。",
    "选用语气": "细节聚焦型",
    "调用标签": ["微调级", "通俗级", "光源与氛围"],
    "整体引导重写": "xxxx，xxxx，xxxx。"
}
```
"""

USER_GUIDE_TEXT_FIELDS = ("场景描述", "整体引导", "摆盘描述", "整体引导重写")
USER_GUIDE_FIELDS = (*USER_GUIDE_TEXT_FIELDS[:3], "调用标签", "整体引导重写")
USER_GUIDE_FIELD_ALIASES = {"整体引导": ("整体描述",)}
USER_GUIDE_SCHEME_COUNT = 1


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_values(text: str) -> List[Any]:
    values: List[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    length = len(text)

    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        value, index = decoder.raw_decode(text, index)
        values.append(value)

    return values


def _load_user_guide_values(text: str) -> List[Any]:
    cleaned = _strip_markdown_fence(text)
    try:
        parsed = json.loads(cleaned)
    except JSONDecodeError as exc:
        try:
            values = _parse_json_values(cleaned)
        except JSONDecodeError:
            raise ValueError(f"User guide API returned invalid JSON: {exc}") from exc
        if not values:
            raise ValueError("User guide API returned empty JSON content.")
        return values

    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _normalize_user_guide_item(parsed: Any) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError("Each user guide entry must be a JSON object.")

    guide: Dict[str, Any] = {}
    for field in USER_GUIDE_TEXT_FIELDS:
        value = parsed.get(field)
        if value is None:
            for alias in USER_GUIDE_FIELD_ALIASES.get(field, ()):
                value = parsed.get(alias)
                if value is not None:
                    break
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"User guide API response must contain a non-empty '{field}' string.")
        guide[field] = value.strip()

    labels = parsed.get("调用标签")
    if not isinstance(labels, list) or not labels:
        raise ValueError("User guide API response must contain a non-empty '调用标签' list.")
    clean_labels = []
    for label in labels:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("User guide API response '调用标签' must contain only non-empty strings.")
        clean_labels.append(label.strip())
    guide["调用标签"] = clean_labels

    return guide


def parse_user_guide_json(text: str) -> List[Dict[str, Any]]:
    values = _load_user_guide_values(text)
    if len(values) != USER_GUIDE_SCHEME_COUNT:
        raise ValueError(
            f"User guide API response must contain exactly {USER_GUIDE_SCHEME_COUNT} guide entries."
        )
    guides = [_normalize_user_guide_item(value) for value in values]
    return guides


def _has_user_guide_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for field in USER_GUIDE_TEXT_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            return False
    labels = value.get("调用标签")
    return isinstance(labels, list) and all(isinstance(label, str) and label.strip() for label in labels)


def has_compose_user_guide(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) == USER_GUIDE_SCHEME_COUNT and all(_has_user_guide_item(item) for item in value)
    return False


def build_user_guide_messages(img_a_b64: str, img_b_b64: str) -> List[Dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": "你是严格的美食摄影引导助手。只能输出合法 JSON，不要输出解释、Markdown 或额外文本。",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_GUIDE_PROMPT_COMPOSE.strip()},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_a_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b_b64}"}},
            ],
        },
    ]


class UserGuideGeneratorModule(PipelineModule):
    """
    Generates a concise user-facing photography guide for an original/generated
    image pair and stores it under PipelineResult.description["user_guide"].
    """

    def __init__(self, model: Optional[str] = None):
        self.model_override = model

    def _get_model(self, context: PipelineContext) -> str:
        return self.model_override or context.config.model_analysis

    def _get_json_path(self, result) -> str:
        if not result.generated_image_path:
            return ""
        return os.path.splitext(result.generated_image_path)[0] + ".json"

    def _try_load_existing_user_guide(self, result) -> bool:
        if isinstance(result.description, dict) and has_compose_user_guide(result.description.get("user_guide")):
            return True

        json_path = self._get_json_path(result)
        if not json_path or not os.path.isfile(json_path):
            return False

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False

        description = data.get("description")
        if not isinstance(description, dict):
            return False

        user_guide = description.get("user_guide")
        if not has_compose_user_guide(user_guide):
            return False

        result.description = dict(result.description or {})
        result.description["user_guide"] = parse_user_guide_json(
            json.dumps(user_guide, ensure_ascii=False)
        )
        return True

    def _generate_user_guide(
        self,
        client: GeminiAPIClient,
        model: str,
        original_image_path: str,
        generated_image_path: str,
    ) -> List[Dict[str, Any]]:
        orig_b64 = image_to_base64(original_image_path)
        gen_b64 = image_to_base64(generated_image_path)
        messages = build_user_guide_messages(orig_b64, gen_b64)
        response = client.generate_with_messages(messages=messages, model=model)
        return parse_user_guide_json(response)

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running User Guide Generator Module...")

        client = None
        model = self._get_model(context)

        for result in context.results:
            if not result.generated_image_path:
                print(f"  -> [SKIP] No generated image for {result.theme}")
                continue

            if self._try_load_existing_user_guide(result):
                print(f"  -> [SKIP] User guide already exists for {result.theme}")
                continue

            try:
                if client is None:
                    client = GeminiAPIClient(
                        api_key=context.config.api_key,
                        base_url=context.config.api_base_url,
                    )

                user_guide = self._generate_user_guide(
                    client=client,
                    model=model,
                    original_image_path=context.original_image_path,
                    generated_image_path=result.generated_image_path,
                )

                result.description = dict(result.description or {})
                result.description["user_guide"] = user_guide

                target_dir = os.path.dirname(result.generated_image_path)
                saved_json = result.save_json(target_dir)
                log_result_saved(
                    call_id=getattr(client, "last_call_id", None),
                    result_path=saved_json,
                    result_kind="json",
                )
                print(f"  -> User guide done for {result.theme}, saved: {os.path.basename(saved_json)}")
            except Exception as e:
                print(f"  -> [ERROR] User guide generation failed on {result.theme}: {e}")

        return context
