import os
import re
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import extract_base64_from_response, base64_to_image, deterministic_seed
from pipeline.utils.parsing import parse_camera_movement


class GenerationModule(PipelineModule):
    """
    Step 2: Iterates over the analyzed schemes and redrawing images.
    Prompt template must contain {suggestion} placeholder.
    Uses deterministic seed from config for reproducible file names.
    """
    def __init__(self, prompt_template: str):
        self.prompt = prompt_template

    def _build_image_path(self, context: PipelineContext, index: int, result) -> str:
        """根据种子确定性地构造图片输出路径"""
        stem = os.path.splitext(os.path.basename(context.original_image_path))[0]
        seed = deterministic_seed(context.config.random_seed, stem)
        theme_part = f"_{result.theme}" if result.theme.strip() else ""
        sname = f"{stem}_p{index+1}{theme_part}_{seed}.jpg"
        sname = re.sub(r'[\\/*?:"<>|]', "", sname)

        rel_path = os.path.relpath(context.original_image_path, context.config.input_directory)
        target_dir = os.path.join(context.config.output_directory, os.path.dirname(rel_path))
        return os.path.join(target_dir, sname)

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Image Generation...")

        client = GeminiAPIClient(
            api_key=context.config.api_key,
            api_key_image=context.config.api_key_image,
            base_url=context.config.api_base_url
        )

        for i, result in enumerate(context.results):
            # 跳过已经成功的
            if result.status == "success":
                continue

            expected_path = self._build_image_path(context, i, result)

            # --- 跳过逻辑：如果图片文件已存在，直接填充路径 ---
            if os.path.isfile(expected_path):
                result.generated_image_path = expected_path
                result.camera_movement = parse_camera_movement(result.original_plan)
                result.status = "generated"
                print(f"  -> [SKIP] Scheme {i+1} image already exists: {os.path.basename(expected_path)}")
                continue

            # --- 正常生成 ---
            try:
                # 1. Ensure base64 is loaded
                if not context.original_image_base64:
                    from pipeline.utils.file_ops import image_to_base64
                    context.original_image_base64 = image_to_base64(context.original_image_path)

                # 2. Format Generation Prompt
                gen_prompt = self.prompt.format(suggestion=result.original_plan)

                # 3. Call Image Gen API
                print(f"  -> Generating Scheme {i+1}: {result.theme[:20]}...")
                response_text = client.generate_image(
                    prompt=gen_prompt,
                    ref_image_base64=context.original_image_base64,
                    model=context.config.model_generation,
                )
                gen_b64 = extract_base64_from_response(response_text)

                if not gen_b64:
                    raise ValueError(f"No image base64 found in response for scheme {i+1}")

                # 4. Save Image
                if base64_to_image(gen_b64, expected_path):
                    result.generated_image_path = expected_path
                    result.status = "generated"
                    result.camera_movement = parse_camera_movement(result.original_plan)
                else:
                    raise IOError(f"Failed to write image to {expected_path}")

            except Exception as e:
                result.status = "failed"
                result.error_message = f"Generation failed: {str(e)}"
                print(f"  -> [ERROR] Scheme {i+1} failed: {e}")

        return context
