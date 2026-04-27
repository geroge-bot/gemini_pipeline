import os
import json
import re
import shutil
import argparse
import sys

# Ensure pipeline is accessible
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.utils.file_ops import deterministic_seed, find_valid_images

DEFAULT_SHORTEN_PROMPT_TEMPLATE = """你将看到一张原图（修改前）、一张基于方案生成的图（修改后）以及原始文字方案。
任务：请根据“修改后”图片实际实现的视觉转变效果，参考原始方案，重新编写一个100字以内的精简摘要，准确反映图片中实际发生的关键改动,包括摆盘变化，机位角度距离变化，构图等。
原始方案如下：{original_plan}
# Output Format
请严格按照以下 XML 结构包裹摘要的内容：

<scheme>
    ### 方案1：[景别角度|主题]（如特写俯拍|XXX主题）
    - **摆盘建议**：[具体对图片美食和其他物体的调整建议]
    - **构图**：[描述构图方法（例如中心构图，三分法等），主体美食在画面中的位置和占比] 
    - **机位**：[相机机位调整]
    - **焦段**：[1X，3X和XX焦段]
    - **角度**：[拍摄俯仰角XX度]
    - **打光**：[打光调整]
    
</scheme>"""

def parse_critique_log(log_path):
    results = {}
    if not os.path.exists(log_path):
        return results
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()

    plans = re.split(r'---\s*Plan\s+(\d+)\s*---', content)
    for i in range(1, len(plans), 2):
        plan_num = int(plans[i])
        plan_text = plans[i+1].strip()
        
        has_error = False
        if "[结果]：Yes" in plan_text:
            has_error = True
            
        error_ids_match = re.search(r'\[错误编号\]：(.*)', plan_text)
        error_ids = error_ids_match.group(1).strip() if error_ids_match else ""
        
        level_match = re.search(r'\[错误等级\]：(.*)', plan_text)
        level = level_match.group(1).strip() if level_match else ""
        if level == "无":
            level = "无错误"
            
        reason_match = re.search(r'\[原因\]：(.*)', plan_text, re.DOTALL)
        reason = reason_match.group(1).strip() if reason_match else ""
        
        raw_output_parts = []
        for line in plan_text.split('\n'):
            if line.startswith('[结果]') or line.startswith('[错误编号]') or line.startswith('[错误等级]') or line.startswith('[原因]'):
                raw_output_parts.append(line.strip())
        raw_output = '\n'.join(raw_output_parts)
            
        results[plan_num] = {
            "has_error": has_error,
            "error_ids": error_ids,
            "level": level,
            "reason": reason,
            "raw_output": raw_output
        }
    return results

def process_directory(input_dir, output_dir):
    orig_output_dir = os.path.join(output_dir, '原图')
    target_output_dir = os.path.join(output_dir, '标注')
    os.makedirs(orig_output_dir, exist_ok=True)
    os.makedirs(target_output_dir, exist_ok=True)
    
    # 遍历input_dir，保持目录结构
    for root, dirs, files in os.walk(input_dir):
        rel_path = os.path.relpath(root, input_dir)
        
        # 建立目标目录结构
        curr_orig_dir = os.path.join(orig_output_dir, rel_path) if rel_path != "." else orig_output_dir
        curr_target_dir = os.path.join(target_output_dir, rel_path) if rel_path != "." else target_output_dir
        
        if not os.path.exists(curr_orig_dir):
            os.makedirs(curr_orig_dir)
        if not os.path.exists(curr_target_dir):
            os.makedirs(curr_target_dir)
            
        for f in files:
            src_path = os.path.join(root, f)
            
            # 分类文件
            if f.endswith('.json'):
                # 处理JSON
                p_match = re.search(r'_p(\d+)', f)
                # 兼容单双下划线的seed匹配
                seed_match = re.search(r'_+?(\d{4,5})\.json$', f)
                if p_match and seed_match:
                    plan_num = int(p_match.group(1))
                    old_seed = seed_match.group(1)
                    stem = f[:p_match.start()]
                    
                    new_seed = deterministic_seed(42, stem)
                    
                    # 查找 critique_log（使用旧Seed寻找）
                    log_filename = f"{stem}_critique_log_{old_seed}.txt"
                    log_path = os.path.join(root, log_filename)
                    critique_results = parse_critique_log(log_path)
                    current_critique = critique_results.get(plan_num, None)
                    
                    # 读取和更新JSON
                    with open(src_path, 'r', encoding='utf-8') as jf:
                        try:
                            data = json.load(jf)
                        except Exception as e:
                            print(f"Error loading {src_path}: {e}")
                            continue
                            
                    if 'prompt' in data:
                        data['analysis_prompt_used'] = data.pop('prompt')
                        
                    original_plan = data.get('original_plan', '')
                    data['shorten_prompt_used'] = DEFAULT_SHORTEN_PROMPT_TEMPLATE.format(original_plan=original_plan)
                    
                    if current_critique:
                        data['validation'] = current_critique
                    
                    # 找到对应的原图文件名
                    # 在同一个输入目录下寻找
                    orig_img_name = ""
                    for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                        if os.path.exists(os.path.join(root, stem + ext)):
                            orig_img_name = stem + ext
                            break
                    
                    if orig_img_name:
                        data['original_image_path'] = os.path.join(curr_orig_dir, orig_img_name)
                    
                    if 'status' not in data:
                        data['status'] = 'success'
                        
                    # 修正生成的图片路径：改为目标标准路径和新Seed文件名
                    # 例如：D:\...\标注\..._p1_NEWSEED.jpg
                    new_image_filename = f"{stem}_p{plan_num}_{new_seed}.jpg"
                    data['generated_image_path'] = os.path.join(curr_target_dir, new_image_filename)
                    data['theme'] = ''

                    # 保存新的JSON (使用新Seed)
                    out_f = f"{stem}_p{plan_num}_{new_seed}.json"
                    out_json_path = os.path.join(curr_target_dir, out_f)
                    with open(out_json_path, 'w', encoding='utf-8') as outf:
                        json.dump(data, outf, ensure_ascii=False, indent=2)
                        
            elif f.endswith('.jpg') or f.endswith('.png'):
                # 判断是否是原图还是生成图形
                p_match = re.search(r'_p(\d+)', f)
                seed_match = re.search(r'_+?(\d{4,5})\.(jpg|png)$', f)
                
                if p_match and seed_match:
                    # 目标图：也需要重命名为新Seed
                    plan_num = int(p_match.group(1))
                    stem = f[:p_match.start()]
                    new_seed = deterministic_seed(42, stem)
                    
                    out_f = f"{stem}_p{plan_num}_{new_seed}.jpg"
                    out_img_path = os.path.join(curr_target_dir, out_f)
                    shutil.copy2(src_path, out_img_path)
                elif '_analysis_' in f:
                    # 分析文本：重命名为新Seed
                    seed_match = re.search(r'_analysis_(\d{4,5})\.txt$', f)
                    if seed_match:
                        stem = f[:f.find('_analysis_')]
                        new_seed = deterministic_seed(42, stem)
                        out_f = f"{stem}_{new_seed}_analysis.txt"
                        out_path = os.path.join(curr_target_dir, out_f)
                        shutil.copy2(src_path, out_path)
                elif '_critique_log_' in f:
                    # 审核日志：重命名为新Seed
                    seed_match = re.search(r'_critique_log_(\d{4,5})\.txt$', f)
                    if seed_match:
                        stem = f[:f.find('_critique_log_')]
                        new_seed = deterministic_seed(42, stem)
                        out_f = f"{stem}_critique_log_{new_seed}.txt"
                        out_path = os.path.join(curr_target_dir, out_f)
                        shutil.copy2(src_path, out_path)
                else:
                    # 原图：不需要Seed处理
                    out_img_path = os.path.join(curr_orig_dir, f)
                    shutil.copy2(src_path, out_img_path)
                    
            elif f.endswith('.txt'):
                # 已经是上面处理过的情况之一了，但如果还有其他的txt
                if '_analysis_' not in f and '_critique_log_' not in f:
                    out_txt_path = os.path.join(curr_orig_dir, f)
                    shutil.copy2(src_path, out_txt_path)
            else:
                target_path = os.path.join(curr_target_dir, f)
                shutil.copy2(src_path, target_path)
                
    print("转换完成！")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert ori dataset to std dataset format")
    parser.add_argument('--input_dir', type=str, required=True, help="Input ori dataset directory")
    parser.add_argument('--output_dir', type=str, required=True, help="Output directory")
    args = parser.parse_args()
    
    process_directory(args.input_dir, args.output_dir)
