import os
import random
from typing import List, Dict, Any, Optional, Tuple

from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.api_usage_logger import log_result_saved
from pipeline.utils.file_ops import image_to_base64


# ==================== 人设库 ====================
PERSONAS = [
    "摄影小白，说话直白，只会说最直观的问题。",
    "追求'氛围感'的女生，说话感性。",
    "爱记录生活的旅行者，提问简洁",
    "严苛的探店博主，对质感要求高",
]


# ==================== 通用问题池 ====================
GENERAL_QUESTIONS = [
    "怎么拍得好看些？",
    "怎么拍的有食欲？",
    "怎么拍出让人喜欢的照片？",
    "为什么拍的这么土？",
]


def _build_question_prompt(persona: str, img_a_b64: str, img_b_b64: str) -> List[Dict[str, Any]]:
    """
    构造 prompt1：基于人设和两张图片，让 Gemini 生成一个口语化提问。
    返回 OpenAI messages 格式的列表。
    """
    return [
        {"role": "system", "content": (
            f"你的人设是：{persona}。你拍了一张照片，但觉得效果不好。\n"
            "请对比你的照片和理想中的效果，提一个【非常简短，极其口语化】的求助问题。\n"
            "要求：1. 限制在15字以内；2. 严禁提到'照片A'、'照片B'或'图A'等代号；\n"
            "3. 请结合你的身份，直接抱怨照片最让你不满的一点。（如：这排骨拍出来像焦炭，一点食欲都没有？、为什么拍的看不出是什么'？）\n"
            "4. 严禁提到'这种'、'那样'、'这种氛围'、'这个效果'等指代词，因为你手里并没有参考图。\n"
        )},
        {"role": "user", "content": [
            {"type": "text", "text": "这是我拍的照片和我想达到的效果，请帮我提问："},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_a_b64}"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b_b64}"}},
        ]},
    ]


def _build_answer_prompt(
    persona: str, question: str, img_a_b64: str, img_b_b64: str
) -> List[Dict[str, Any]]:
    """
    构造 prompt2：基于人设、问题和两张图片，让 Gemini 生成专业回复。
    返回 OpenAI messages 格式的列表。
    """
    return [
        {"role": "system", "content": (
            "你是一位深耕美食摄影与AI视觉生成的跨界导师。你的任务是分析原图（图一）缺陷，并以成片效果（图二）为逻辑终点，结合用户提问，提供摆盘推荐逻辑、实拍方案与一套精准的AI重绘指令。\n\n"
            "### 核心原则：\n"
            "1. **身份沉浸**：严禁提及'照片A/B'、'对比'或'参考图'。直接指出痛点并给出方案，语气犀利专业。\n"
            "2. **物理锚点**：建议必须锚定于原图已有元素。禁止改变场景或地点，必须将场景缺陷转化为风格优势。\n"
            "3. **去痕迹化**：禁止描述具体色彩参数。必须使用风格化词汇，且指令为流畅的自然语言，严禁符号化输出。\n\n"
            "4. **语言风格 (Prompt Engineering)**：使用【终态描述】而非动作指令。多使用空间方位词、光学现象词和物理重力词。避免含义模糊的形容词（如'漂亮的'），改用具体的视觉标签。\n\n"
            "### 注意事项：\n"
            "1. 45度以下不算俯拍，15-30度算低角度平拍。\n"
            "2. 菜品摆放注意添加盘子，碗等容器，禁止直接称呼主体。如大虾盘子，不要称呼大虾。如不要说\"把大虾放在前景\"而是说\"把盛大虾的盘子放在前景\"，或\"一盘生肉放在XX\"。\n"
            "### 风格与滤镜协议：\n"
            "从库中检索匹配项（无倾向选【无滤镜/原画感】）：\n"
            "[无滤镜/原画感 | 日系清透 | 港式复古 | 美式复古 | CCD千禧风 | 电影感 | 德式冷调 | 富士系 | 莫兰迪 | 奶油柔焦 | 油画感 | 赛博朋克 | 经典黑白]。\n\n"
            "### 输出维度要求：\n"
            "- **原片诊断 (analysis)**：指出原图在物理逻辑（支撑感缺失、重心偏移）或审美张力上的核心痛点。\n"
            "- **手机操作步骤 (adjustment_user)**：语气亲切易懂。分步骤引导人完成摆盘，机位，构图，焦段变化操作。只需要明确简练的描述动作对象和指令，不需要描述美学目的。避免使用专业词汇如伦勃朗光，营造浅景深等。限40字以内。\n"
            "- **优化理由（thinking）**：先分析如何选择拍摄主体，以及拍摄的大致菜品数量。基于分析信息，针对原图的问题，展现优化效果摘要的内在推导过程，用自然语言阐述为了解决XX问题，并基于该信息如何改变具体的机位、摆盘等调整策略的美学逻辑和原因，60字左右。\n"
            "- **AI指令 (adjustment_aigc)**：140字以内，，准确反映图片中实际发生的关键改动。**请严格遵循以下句式模板撰写**：\n"
            "  **[模板]**：第一句话描述目标画面效果整体视觉，包括机位与摆盘。描述摆盘变化（具体对图片中美食和其他物体的调整，包括摆放位置、角度、删减以及目标效果的调整描述，需进行详细描述）和目标画面的构图方法（例如中心构图，三分法等，以及主体美食在画面中的位置和占比）。目标画面相机机位，拍摄俯仰角度(数值形式)，和焦段（简单描述焦段即可）。目标画面的打光变化，单独一句描述，小于20字。画面呈现[风格滤镜]与[光学质感]。\n"
            "  **[约束]**："
            "  1. 45度以下不算俯拍，15-30度算低角度平拍。"
            "  2. 菜品摆放注意添加盘子，碗等容器，禁止直接称呼主体。如大虾盘子，不要称呼大虾。如不要说\"把大虾放在前景\"而是说\"把盛大虾的盘子放在前景\"，或\"一盘生肉放在XX\"。\n"
            "  3. 注意目标效果是否使用了中心构图、对焦构图、三分构图和框架构图，如果使用了对应的构图，在描述菜品目标摆放位置时，尽量使用中心点、三分点（如左上角三分点、左下三分点、右上三分点、右下三分点）、三分区域和占比进行精确描述。\n"
            "  4. 如果目标图有意对主要的菜品使用了切盘拍摄方法，需要在描述菜品位置时同时进行描述。\n\n"
            "### 输出JSON格式：\n"
            "{\n"
            "  \"analysis\": \"诊断意见\",\n"
            "  \"object_data\": {\n"
            "    \"subject_feature\": \"描述整体食物的菜系，以及图片中主体菜品的菜名、位置、外观等特征\",\n"
            "    \"scenery_feature\": \"描述餐厅、环境、入镜的人和光线等环境信息\",\n"
            "    \"img1_object_pos\": \"主体食品餐盘在图一中的九宫格位置及占比\",\n"
            "    \"img1_object_count\": \"(int)图一中食品餐盘（以一盘、一杯等为单位，不要计数餐盘内的食物数量）的数量\",\n"
            "    \"img1_camera_angle\": \"(int)图一的相机拍摄角度，以水平为0度，俯拍为90度\",\n"
            "    \"img2_object_pos\": \"主体食品餐盘在图二中的九宫格位置及占比\",\n"
            "    \"img2_object_count\": \"(int)图二中食品餐盘（以一盘、一杯等为单位，不要计数餐盘内的食物数量）的数量\",\n"
            "    \"img2_camera_angle\": \"(int)图二的相机拍摄角度，以水平为0度，俯拍为90度\",\n"
            "  },\n"
            "  \"thinking\": \"AI指令思考过程\",\n"
            "  \"adjustment_aigc\": \"AI理想状态重绘指令\",\n"
            "  \"adjustment_user\": \"手机操作步骤\",\n"
            "  \"tips\": [\"技巧1\", \"技巧2\"]\n"
            "}"
        )},
        {"role": "user", "content": [
            {"type": "text", "text": f"用户身份：{persona}\n用户提问：'{question}'"},
            {"type": "text", "text": "待改进的原图："},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_a_b64}"}},
            {"type": "text", "text": "目标效果（严禁在回复中提及此图）："},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b_b64}"}},
        ]},
    ]


def _strip_images_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    从 messages 列表中移除 base64 图片数据，仅保留文本内容，
    用于将对话历史序列化到 JSON 而不膨胀文件体积。
    """
    cleaned: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part)
                elif part.get("type") == "image_url":
                    text_parts.append({"type": "text", "text": "[image]"})
            cleaned.append({"role": msg["role"], "content": text_parts})
        else:
            cleaned.append(msg)
    return cleaned


class DescriptionModule(PipelineModule):
    """
    Description module: for each (original, generated) image pair, constructs a
    user question and then generates a professional photography coaching response.

    Workflow:
      1. Question construction:
         - 30% chance: randomly sample from GENERAL_QUESTIONS.
         - 70% chance: sample a persona from PERSONAS, build prompt1 with
           the persona and two images, call Gemini to generate the question.
      2. Answer generation:
         - Build prompt2 with the question, persona, and two images.
         - Call Gemini to produce the structured JSON response.
      3. Record & return the full conversation history of both steps.
    """

    def __init__(self, model: Optional[str] = None):
        """
        Args:
            model: Model name for text generation (defaults to config.model_analysis).
        """
        self.model_override = model

    def _get_model(self, context: PipelineContext) -> str:
        return self.model_override or context.config.model_analysis

    def _generate_question(
        self,
        client: GeminiAPIClient,
        model: str,
        img_a_b64: str,
        img_b_b64: str,
    ) -> Tuple[str, str, List[Dict[str, Any]]]:
        """
        构造用户提问。

        Returns:
            (question, persona, conversation_history_step1)
            - 若走通用问题分支，persona 为空字符串，history 为空列表。
        """
        if random.random() < 0.3:
            # ---- 30% 直接采样通用问题 ----
            question = random.choice(GENERAL_QUESTIONS)
            return question, "", []

        # ---- 70% 通过 Gemini 构造 ----
        persona = random.choice(PERSONAS)
        prompt1_messages = _build_question_prompt(persona, img_a_b64, img_b_b64)
        question = client.generate_with_messages(
            messages=prompt1_messages,
            model=model,
        ).strip()
        return question, persona, prompt1_messages

    def _generate_answer(
        self,
        client: GeminiAPIClient,
        model: str,
        persona: str,
        question: str,
        img_a_b64: str,
        img_b_b64: str,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        基于问题和两张图片调用 Gemini 获取专业回复。

        Returns:
            (answer_text, prompt2_messages)
        """
        prompt2_messages = _build_answer_prompt(persona, question, img_a_b64, img_b_b64)
        answer = client.generate_with_messages(
            messages=prompt2_messages,
            model=model,
        )
        return answer, prompt2_messages

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Description Module...")

        client = GeminiAPIClient(
            api_key=context.config.api_key,
            base_url=context.config.api_base_url,
        )
        model = self._get_model(context)

        for result in context.results:
            # 跳过没有生成图片的 scheme
            if not result.generated_image_path:
                print(f"  -> [SKIP] No generated image for {result.theme}")
                continue

            # 跳过已有 description 的 result（支持断点续跑）
            if result.description is not None:
                print(f"  -> [SKIP] Description already exists for {result.theme}")
                continue

            try:
                # 加载两张图的 base64
                orig_b64 = image_to_base64(context.original_image_path)
                gen_b64 = image_to_base64(result.generated_image_path)

                # ===== Step 1: 构造问题 =====
                question, persona, step1_messages = self._generate_question(
                    client, model, orig_b64, gen_b64,
                )
                print(f"  -> Question: {question}")

                # ===== Step 2: 生成回答 =====
                answer, step2_messages = self._generate_answer(
                    client, model, persona, question, orig_b64, gen_b64,
                )

                # ===== 组装完整对话历史（去除图片数据以节省存储） =====
                full_history: List[Dict[str, Any]] = []
                if step1_messages:
                    full_history.extend(_strip_images_from_messages(step1_messages))
                    full_history.append({"role": "assistant", "content": question})
                full_history.extend(_strip_images_from_messages(step2_messages))
                full_history.append({"role": "assistant", "content": answer})

                # ===== 存储到 result =====
                result.description = {
                    "persona": persona,
                    "question": question,
                    "answer": answer,
                    "conversation_history": full_history,
                }

                # 保存 JSON（与生成图片同目录）
                target_dir = os.path.dirname(result.generated_image_path)
                saved_json = result.save_json(target_dir)
                log_result_saved(
                    call_id=getattr(client, "last_call_id", None),
                    result_path=saved_json,
                    result_kind="json",
                )
                print(f"  -> Description done for {result.theme}, saved: {os.path.basename(saved_json)}")

            except Exception as e:
                print(f"  -> [ERROR] Description failed on {result.theme}: {e}")

        return context
