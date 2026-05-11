import json
import os
from json import JSONDecodeError
from typing import Any, Dict, List, Optional

from pipeline.interfaces import PipelineContext, PipelineModule
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import image_to_base64


LABELING_PROMPT = """对两张图进行打标，其中图一为输入图，图二为输出图，以json格式直接输出对应的结果，不要输出其他内容。

输入图：
拍摄角度：（使用int数值输出，俯拍为90）
菜品种类：中餐，日式料理，西餐，火锅，下午茶，家庭烹饪，烧烤烤肉，韩式来哦里，东南亚菜，野餐露营
拍摄场景：窗边，居家，餐厅，室外，室内
光线：顶灯，自然光，其他
色温：低色温，中色温，高色温，混合色温
菜品数量：1，2，3，4，5，6+

输出图：
拍摄角度：（使用int数值输出，俯拍为90）
景别：特写，中近景，远景
拍摄角度方法：平拍，斜拍，俯拍，其他
构图&摆盘：中心构图，偏中心构图，对角线构图，三角形构图，S形构图，框架构图，留白构图，其他
景别：近景，中景，远景
互动：无互动，切，夹，叉，倒，举杯，其他
新增餐具：无新增，筷子，刀，叉，瓷勺，不锈钢勺，其他
美学评分：1-5分

效果美观度
效果符合大众审美，好看程度
1分
极差。整体效果让人非常反感，非常不
好看，存在严重效果质量问题
2分
较差。整体效果观感不美观，存在一些
明显的审美缺陷
3分
一般。满足基本的视觉审美，但缺乏亮
点，没有推荐分享的欲望
4分
良好。整体效果还不错，可圈可点，但
还有一些可以提升的点
5分
极好。整体效果一眼惊艳，各维度效果
都令人满意"""


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


def parse_labeling_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_markdown_fence(text)
    try:
        parsed = json.loads(cleaned)
    except JSONDecodeError as exc:
        raise ValueError(f"Labeling API returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Labeling API returned JSON, but the top-level value is not an object.")
    return parsed


def build_labeling_messages(img_a_b64: str, img_b_b64: str) -> List[Dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": "你是严格的美食图片打标助手。只能输出合法 JSON，不要输出解释、Markdown 或额外文本。",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": LABELING_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_a_b64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b_b64}"}},
            ],
        },
    ]


class TwoImageLabelingModule(PipelineModule):
    """
    Labels an original/generated image pair and stores the parsed JSON on
    PipelineResult.labels.
    """

    def __init__(self, model: Optional[str] = None):
        self.model_override = model

    def _get_model(self, context: PipelineContext) -> str:
        return self.model_override or context.config.model_analysis

    def _label_pair(
        self,
        client: GeminiAPIClient,
        model: str,
        original_image_path: str,
        generated_image_path: str,
    ) -> Dict[str, Any]:
        orig_b64 = image_to_base64(original_image_path)
        gen_b64 = image_to_base64(generated_image_path)
        messages = build_labeling_messages(orig_b64, gen_b64)
        response = client.generate_with_messages(messages=messages, model=model)
        return parse_labeling_json(response)

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Two Image Labeling Module...")

        client = GeminiAPIClient(
            api_key=context.config.api_key,
            base_url=context.config.api_base_url,
        )
        model = self._get_model(context)

        for result in context.results:
            if not result.generated_image_path:
                print(f"  -> [SKIP] No generated image for {result.theme}")
                continue

            if result.labels is not None:
                print(f"  -> [SKIP] Labels already exist for {result.theme}")
                continue

            try:
                result.labels = self._label_pair(
                    client=client,
                    model=model,
                    original_image_path=context.original_image_path,
                    generated_image_path=result.generated_image_path,
                )

                target_dir = os.path.dirname(result.generated_image_path)
                saved_json = result.save_json(target_dir)
                print(f"  -> Labels done for {result.theme}, saved: {os.path.basename(saved_json)}")
            except Exception as e:
                print(f"  -> [ERROR] Labeling failed on {result.theme}: {e}")

        return context
