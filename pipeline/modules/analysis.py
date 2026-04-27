import os
import json
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.parsing import parse_schemes_universal
from pipeline.utils.file_ops import image_to_base64, deterministic_seed
from pipeline.models import PipelineResult


class AnalysisModule(PipelineModule):
    """
    Step 1: Analyzes the original image and returns multiple photographic schemes.
    Saves analysis_text to a .txt file for skip/resume support.
    """
    def __init__(self, prompt_template: str):
        self.prompt = prompt_template

    def _get_analysis_txt_path(self, context: PipelineContext) -> str:
        """根据种子和图片名，返回 analysis_text 的确定性保存路径"""
        stem = os.path.splitext(os.path.basename(context.original_image_path))[0]
        seed = deterministic_seed(context.config.random_seed, stem)
        rel_path = os.path.relpath(context.original_image_path, context.config.input_directory)
        target_dir = os.path.join(context.config.output_directory, os.path.dirname(rel_path))
        return os.path.join(target_dir, f"{stem}_{seed}_analysis.txt")

    def _try_load_existing(self, context: PipelineContext) -> bool:
        """
        尝试从已有的 analysis txt 文件恢复 context。
        返回 True 表示跳过分析阶段。
        """
        txt_path = self._get_analysis_txt_path(context)
        if not os.path.isfile(txt_path):
            return False

        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                analysis_text = f.read().strip()

            if not analysis_text:
                return False

            # 成功读取，恢复 context
            context.analysis_text = analysis_text
            context.parsed_schemes = parse_schemes_universal(analysis_text)

            for scheme in context.parsed_schemes[:4]:
                res = PipelineResult(
                    theme=scheme['theme'],
                    original_image_path=context.original_image_path,
                    original_plan=scheme['content'],
                    analysis_prompt_used=self.prompt,
                    mode=context.config.shorten_mode,
                    status="analyzed"
                )
                context.results.append(res)

            return True
        except Exception as e:
            print(f"  -> [WARN] Failed to load existing analysis: {e}")
            return False

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Analysis Module...")

        # --- 跳过逻辑：检查已有的 analysis txt ---
        if self._try_load_existing(context):
            print(f"[{img_name}] [SKIP] Analysis already exists ({len(context.parsed_schemes)} schemes loaded from txt)")
            return context

        # --- 正常执行分析 ---
        # 1. Load Image Base64
        if not context.original_image_base64:
            context.original_image_base64 = image_to_base64(context.original_image_path)

        # 2. Call Gemini
        client = GeminiAPIClient(api_key=context.config.api_key, base_url=context.config.api_base_url)
        analysis_text = client.generate_text(
            prompt=self.prompt,
            image_base64=context.original_image_base64,
            model=context.config.model_analysis,
        )

        if not analysis_text:
            raise RuntimeError("Analysis API returned empty response.")

        context.analysis_text = analysis_text
        context.parsed_schemes = parse_schemes_universal(analysis_text)
        print(f"[{img_name}] Found {len(context.parsed_schemes)} schemes.")

        # 3. 保存 analysis_text 到 txt 文件
        txt_path = self._get_analysis_txt_path(context)
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(analysis_text)
        print(f"[{img_name}] Saved analysis text to: {os.path.basename(txt_path)}")

        # 4. Create initial PipelineResult stubs
        for scheme in context.parsed_schemes[:4]:  # Max 4 schemes
            res = PipelineResult(
                theme=scheme['theme'],
                original_image_path=context.original_image_path,
                original_plan=scheme['content'],
                analysis_prompt_used=self.prompt,
                mode=context.config.shorten_mode,
                status="analyzed"
            )
            context.results.append(res)

        return context
