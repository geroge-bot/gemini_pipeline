import os
import json
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import image_to_base64
from pipeline.utils.parsing import clean_xml_markdown


class ShortenModule(PipelineModule):
    """
    Step 3: Compresses huge descriptions into short UI plans.
    Prompt template must contain {suggestion} placeholder.
    Supports skip/resume: checks existing JSON for short_plan before calling API.
    """
    def __init__(self, prompt_template: str):
        self.prompt = prompt_template

    def _get_json_path(self, result) -> str:
        """返回该 result 对应的 JSON 文件路径（与图片同名 .json）"""
        if not result.generated_image_path:
            return ""
        base = os.path.splitext(result.generated_image_path)[0]
        return base + ".json"

    def _try_load_existing_short_plan(self, result) -> bool:
        """
        检查已有 JSON 文件中是否已包含 short_plan。
        如果有，恢复到 result 中并返回 True。
        """
        json_path = self._get_json_path(result)
        if not json_path or not os.path.isfile(json_path):
            return False

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            short_plan = data.get("short_plan", "")
            if short_plan and short_plan.strip():
                result.short_plan = short_plan
                result.shorten_prompt_used = data.get("shorten_prompt_used", "")
                result.status = "success"
                return True
        except Exception:
            pass

        return False

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Shortener...")
        client = GeminiAPIClient(api_key=context.config.api_key, base_url=context.config.api_base_url)

        for result in context.results:
            # 跳过没有生成图片的 scheme
            if not result.generated_image_path:
                continue

            # --- 跳过逻辑：检查已有 JSON 中的 short_plan ---
            if self._try_load_existing_short_plan(result):
                json_path = self._get_json_path(result)
                print(f"  -> [SKIP] Short plan already exists for {result.theme}: {os.path.basename(json_path)}")
                continue

            # --- 正常执行缩短 ---
            try:
                prompt = self.prompt.format(suggestion=result.original_plan)

                if context.config.shorten_mode == 'C' and result.generated_image_path:
                    gen_b64 = image_to_base64(result.generated_image_path)
                    short_plan_raw = client.generate_text(prompt=prompt, image_base64=gen_b64, model=context.config.model_shorten)
                else:
                    short_plan_raw = client.generate_text(prompt=prompt, model=context.config.model_shorten)

                result.short_plan = clean_xml_markdown(short_plan_raw)
                result.shorten_prompt_used = prompt
                result.status = "success"

                # 保存 JSON
                target_dir = os.path.dirname(result.generated_image_path)
                saved_json = result.save_json(target_dir)
                print(f"  -> Saved JSON pair: {os.path.basename(saved_json)}")

            except Exception as e:
                result.status = "failed"
                result.error_message = f"Shorten failed: {str(e)}"
                print(f"  -> [ERROR] Shortener failed on {result.theme}: {e}")

        return context
