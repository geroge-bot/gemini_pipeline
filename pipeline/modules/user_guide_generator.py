import json
import os
from json import JSONDecodeError
from typing import Any, Dict, List, Optional

from pipeline.interfaces import PipelineContext, PipelineModule
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.api_usage_logger import log_result_saved
from pipeline.utils.file_ops import image_to_base64


USER_GUIDE_PROMPT_COMPOSE = """
你是一位深耕美食摄影与AI视觉生成的跨界导师。你的任务是给用户简短的教学，让用户能够指导怎么从当前的相机画面（图一）能够拍出目标的效果图（图二）。

规则：
1. 分为3个描述：
    a. 场景描述，按照分为环境 + 菜系的固定格式给出，菜系分为：中餐、东南亚菜、韩餐、烧烤、西餐、下午产、日料、水果、饮品。（从中选择最合适的一个作为分类）。场景描述示例：暖光中餐、圆桌中餐、包厢中餐、暗光西餐、烛光西餐、露台西餐、窗边下午茶、阳光下午茶、露台下午茶、明档烘培蛋糕、板前日料、暗调日料、吧台日料。共4-7字，不要包含任何的标点符号，如果出现中餐与日料难以分辨的情形，以中餐为主)；
    b. 整体引导，语气亲切易懂，分为三句描述：
        i) 第一句为摆盘引导，使用清晰且情切的描述，告诉用户该怎么进行摆盘操作，话语需要基友信息量
        ii) 第二句为拍摄方法引导，指导用户进行机位调整，如果出现虚化，需要提示用户怎么才能拍出虚化，例如，使用长焦拍出虚化
        iii) 第三句为拍摄效果（按照动词 + 食物 + XX感的形式给出，例如，拍出饮品通透感、展现火锅的烟气），总共20-25字，不超过上限；
    c. 除了整体引导外，还需要输出一个整体引导的重写，让更加格式多样化（改成2-4句，不一定完全遵从格式，但需要包含三个信息）和人性化(25-30字)
    d. 摆盘描述，单独对如何进行摆盘进行引导，只包含与摆盘相关的内容(25-30字)。严格控制字数不要超过上限（包括中文、标点）。
2. 融合对场景的理解、分析，以及对应的拍摄建议。
3. 使用中文输出。

什么是好的引导语：
1. 低认知负荷、简单的、直接的、行动导向的引导。
2. 引导部分动作明确，使用具体的名词和动词，不要使用“主体”等抽象词语。
3. 指令简介，分步指导。

输出格式：
json```
{
    "场景描述": xxxx(5-8字，不输出结尾标点符号)，
    "整体引导": xxxx(20-25字，使用逗号连接，句号结尾),
    "摆盘描述": xxxx(25-30字，使用逗号连接，句号结尾),
    "整体引导重写"：xxxx(20-25字，使用逗号连接，句号结尾),
}  
```
"""

USER_GUIDE_FIELDS = ("场景描述", "整体引导", "摆盘描述", "整体引导重写")
USER_GUIDE_FIELD_ALIASES = {"整体引导": ("整体描述",)}


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


def parse_user_guide_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_markdown_fence(text)
    try:
        parsed = json.loads(cleaned)
    except JSONDecodeError as exc:
        raise ValueError(f"User guide API returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("User guide API returned JSON, but the top-level value is not an object.")

    guide: Dict[str, Any] = {}
    for field in USER_GUIDE_FIELDS:
        value = parsed.get(field)
        if value is None:
            for alias in USER_GUIDE_FIELD_ALIASES.get(field, ()):
                value = parsed.get(alias)
                if value is not None:
                    break
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"User guide API response must contain a non-empty '{field}' string.")
        guide[field] = value.strip()

    return guide


def has_compose_user_guide(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(value.get(field), str) and value[field].strip() for field in USER_GUIDE_FIELDS)


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
        result.description["user_guide"] = {field: user_guide[field].strip() for field in USER_GUIDE_FIELDS}
        return True

    def _generate_user_guide(
        self,
        client: GeminiAPIClient,
        model: str,
        original_image_path: str,
        generated_image_path: str,
    ) -> Dict[str, Any]:
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
