import os
import json
import re
import concurrent.futures
from tqdm import tqdm

from pipeline.models import PipelineConfig, PipelineResult
from pipeline.interfaces import PipelineContext
from pipeline.modules.generation import GenerationModule
from pipeline.utils.prompt_manager import PromptManager
from pipeline.utils.file_ops import deterministic_seed

def parse_theme(plan_text: str) -> str:
    """从方案文本中解析出主题"""
    m = re.search(r"方案.*?：\[.*?\|([^\]]+)\]", plan_text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"方案.*?：\[(.+?)\]", plan_text)
    if m2:
        return m2.group(1).strip()
    return ""

def process_single_image(item: dict, image_root: str, output_dir: str, config: PipelineConfig, prompt_template: str):
    rel_img_path = item.get("image")
    if not rel_img_path:
        return
        
    abs_img_path = os.path.join(image_root, rel_img_path)
    if not os.path.exists(abs_img_path):
        print(f"Image not found, skipping: {abs_img_path}")
        return
        
    # 组装 Context
    context = PipelineContext(config, abs_img_path)
    generation_module = GenerationModule(prompt_template=prompt_template)
    
    # 将所有的 plan 拼成 analysis_text
    plans = item.get("predict_plan", [])
    context.analysis_text = "\n\n".join(plans)
    
    # 写入 analysis.txt (FILE_FORMAT.md 规范)
    stem = os.path.splitext(os.path.basename(abs_img_path))[0]
    seed = deterministic_seed(config.random_seed, stem)
    
    rel_path_dir = os.path.dirname(os.path.relpath(abs_img_path, image_root))
    target_dir = os.path.join(output_dir, rel_path_dir)
    os.makedirs(target_dir, exist_ok=True)
    
    analysis_txt_path = os.path.join(target_dir, f"{stem}_{seed}_analysis.txt")
    if not os.path.exists(analysis_txt_path):
        with open(analysis_txt_path, 'w', encoding='utf-8') as f:
            f.write(context.analysis_text)
    
    # 构建 Results
    for idx, plan_text in enumerate(plans):
        theme = parse_theme(plan_text)
        
        result = PipelineResult(
            theme=theme,
            original_image_path=abs_img_path,
            original_plan=plan_text,
            analysis_prompt_used="N/A (Loaded from JSON dataset)",
            mode="C", 
            status="analyzed", # 标记为分析完成，准备生成
        )
        context.results.append(result)
        
    # 调用生成模块
    context = generation_module.process(context)
    
    # 处理结果并保存 JSON
    for result in context.results:
        if result.status == "generated" or result.status == "success":
            # 把最后的状态更新为 success，因为这里不需要进入 shorten
            result.status = "success" 
            if result.generated_image_path:
                out_dir = os.path.dirname(result.generated_image_path)
                result.save_json(out_dir)
        else:
            print(f"Failed to generate for {abs_img_path}, theme: {result.theme}, error: {result.error_message}")

def main():
    json_path = r"D:\美食测试集\美食 - 测试数据\caffee_260331_short_测试集_convert.json"
    image_root = r"D:\美食测试集\美食 - 测试数据\20260330-美食-测试集"
    # 输出目录，保持和原图相对结构
    output_dir = r"D:\美食测试集\美食 - 测试数据\20260330-美食-测试集_output"
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 获取生成模型的系统 prompt
    prompts = PromptManager.get_all_prompts()
    prompt_template = None
    for cat in prompts.values():
        if "PROMPT_DRAW_IMAGE" in cat:
            prompt_template = cat["PROMPT_DRAW_IMAGE"]
            break
            
    if not prompt_template:
        raise ValueError("PROMPT_DRAW_IMAGE not found in prompt manager")

    # 构建配置
    config = PipelineConfig(
        input_directory=image_root,
        output_directory=output_dir,
    )
    
    print(f"Total images to process: {len(data)}")
    print(f"Using {config.max_workers} threads for processing.")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(process_single_image, item, image_root, output_dir, config, prompt_template) for item in data]
        for _ in tqdm(concurrent.futures.as_completed(futures), total=len(data), desc="Processing images"):
            pass

if __name__ == "__main__":
    main()
