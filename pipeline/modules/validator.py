import os
import json
import re
from typing import Dict, Any
import re
from PIL import Image, ImageDraw, ImageFont
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import image_to_base64

class ValidatorModule(PipelineModule):
    """
    Quality inspection module that compares original and generated images.
    Extracts validation results and visualizations.
    """
    def __init__(self, prompt_template: str):
        self.prompt = prompt_template
        self.font_path = "C:/Windows/Fonts/simhei.ttf"

    def _get_font(self, size):
        try:
            return ImageFont.truetype(self.font_path, size)
        except IOError:
            try:
                # Fallback
                return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
            except IOError:
                return ImageFont.load_default()

    def process(self, context: PipelineContext) -> PipelineContext:
        img_name = os.path.basename(context.original_image_path)
        print(f"[{img_name}] Running Validator...")
        client = GeminiAPIClient(api_key=context.config.api_key, base_url=context.config.api_base_url)

        error_visual_dir = os.path.join(context.config.output_directory, "error_visual_results")
        os.makedirs(error_visual_dir, exist_ok=True)

        for result in context.results:
            if not result.generated_image_path:
                print(f"  -> [SKIP] No generated image for {result.theme}")
                continue
                
            if result.validation is not None:
                print(f"  -> [SKIP] Validation already exists for {result.theme}")
                continue
            
            try:
                orig_b64 = image_to_base64(context.original_image_path)
                gen_b64 = image_to_base64(result.generated_image_path)
                
                # Check if visual inspection was already done to support skip/resume
                # We won't skip for now assuming validating is cheap or explicitly requested,
                # but let's parse raw output anyway.
                
                prompt = self.prompt
                # Model shorten or analysis is text-vision, we use shorten as default text model here
                validation_raw = client.generate_text(prompt=prompt, image_base64=[orig_b64, gen_b64], model=context.config.model_shorten)
                
                res_match = re.search(r'\[结果\].*?(Yes|No)', validation_raw, re.IGNORECASE)
                has_error = res_match.group(1).lower() == 'yes' if res_match else False
                
                err_id_match = re.search(r'\[错误编号\].*?([0-9,，\s]+)', validation_raw)
                err_ids = err_id_match.group(1).strip() if err_id_match else "0"
                err_ids_clean = "_".join(re.findall(r'\d+', err_ids))
                if not err_ids_clean: err_ids_clean = "0"

                level_match = re.search(r'\[错误等级\].*?(轻微|中等|严重|无错误)', validation_raw)
                level = level_match.group(1).strip() if level_match else "未知"
                
                reason_match = re.search(r'\[原因\][：:](.*)', validation_raw, re.DOTALL)
                reason = reason_match.group(1).strip() if reason_match else ""
                
                val_data = {
                    "has_error": has_error,
                    "error_ids": err_ids,
                    "level": level,
                    "reason": reason,
                    "raw_output": validation_raw
                }
                
                result.validation = val_data
                result.save_json(os.path.dirname(result.generated_image_path))
                
                image_key = os.path.basename(result.generated_image_path)
                
                if has_error:
                    vis_filename = f"ERR_{err_ids_clean}_{image_key}"
                    vis_path = os.path.join(error_visual_dir, vis_filename)
                    self._create_visualization(result.generated_image_path, vis_path, err_ids, level, reason)
                
                print(f"  -> Validation done for {result.theme}. Has error: {has_error}")
            
            except Exception as e:
                print(f"  -> [ERROR] Validator failed on {result.theme}: {e}")

        return context

    def _create_visualization(self, image_path: str, save_path: str, err_ids: str, level: str, reason: str):
        img = Image.open(image_path)
        width, height = img.size
        
        font_size = max(24, int(width * 0.02))  # Scale font size with image width somewhat
        font = self._get_font(font_size)
        
        text_content = f"错误编号: {err_ids} | 等级: {level}\n原因: {reason}"
        
        lines = []
        for line in text_content.split('\n'):
            words = list(line)
            current_line = ""
            for word in words:
                try:
                    # Uses getbbox if available in newer PIL
                    left, top, right, bottom = font.getbbox(current_line + word)
                    w = right - left
                except AttributeError:
                    w = font.getsize(current_line + word)[0]
                
                if w > width - int(font_size * 2):
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line += word
            if current_line:
                lines.append(current_line)
            
        line_height = int(font_size * 1.5)
        panel_height = len(lines) * line_height + font_size * 2
        
        new_img = Image.new('RGB', (width, height + panel_height), color=(255, 255, 255))
        new_img.paste(img, (0, 0))
        
        draw = ImageDraw.Draw(new_img)
        y_text = height + font_size
        for line in lines:
            draw.text((font_size, y_text), line, font=font, fill=(255, 0, 0))
            y_text += line_height
            
        new_img.save(save_path, quality=95)
