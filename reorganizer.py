import os
import json
import shutil
import re
from pathlib import Path

# Configuration
SOURCE_DIRS = [
    r"F:\咖啡厅2\咖啡厅2",
    r"F:\美食大众点评0212—6677",
    r"F:\全景数据_0212—6677"
]
TARGET_ROOT = r"D:\美食_标准格式"

# Patterns
# Analysis: stem_analysis_seed.txt
# Generated: stem_pN_..._seed.jpg/json
# Original: stem.png/jpg (everything else)

RE_ANALYSIS = re.compile(r"^(.*)_analysis_(\d{4})\.txt$", re.IGNORECASE)
RE_RESULT = re.compile(r"^(.*)_p(\d)_(.*)_(\d{4})\.(jpg|json)$", re.IGNORECASE)
RE_RESULT_DOUBLE_UNDER = re.compile(r"^(.*)_p(\d)__(\d{4})\.(jpg|json)$", re.IGNORECASE)

def standardize_json(source_data, original_path, generated_path, theme=""):
    """Convert non-standard source JSON to standard schema."""
    # Ensure original_plan is a clean string
    original_plan = source_data.get("original_plan", "")
    if isinstance(original_plan, dict):
        original_plan = json.dumps(original_plan, ensure_ascii=False, indent=2)
    
    # Cleanup trailing junk if it looks like a truncated stringified JSON
    # e.g. '",\n    "layout": "...",\n  },\n  {\n    "plan_name": "'
    if isinstance(original_plan, str):
        # Remove trailing JSON fragments
        original_plan = re.sub(r'\\?",\s*\n\s*\\"layout\\?":.*$', '', original_plan, flags=re.DOTALL)
        original_plan = re.sub(r'\s*},\s*\n\s*{\s*"?plan_name"?.*$', '', original_plan, flags=re.DOTALL)
        original_plan = original_plan.strip().strip('"').strip("'")

    # Theme might be in the source data
    final_theme = source_data.get("theme", theme)
    if not final_theme or final_theme.startswith("方案_") or str(final_theme).lower() == "null":
         # Try to extract theme from original_plan if it looks like "### 方案1：主题"
         # Handle both half-width and full-width colons, and optional brackets
         match = re.search(r"###\s*方案\d[:：]\s*\[?(.*?)\]?($|\n|\\n|\"|,)", original_plan)
         if match:
             final_theme = match.group(1).strip()
    
    # Final cleanup of theme
    if final_theme:
        # Remove trailing junk like ", or ", or similar
        final_theme = re.sub(r'["\',].*$', '', str(final_theme)).strip()
    
    standard = {
        "theme": final_theme,
        "original_image_path": str(original_path),
        "generated_image_path": str(generated_path) if generated_path else None,
        "original_plan": original_plan,
        "short_plan": source_data.get("short_plan"),
        "analysis_prompt_used": "", # Unknown
        "shorten_prompt_used": None,
        "mode": source_data.get("mode", "C"),
        "camera_movement": None, # Could extract but usually None in source
        "status": "success",
        "error_message": None
    }
    return standard

def process():
    os.makedirs(TARGET_ROOT, exist_ok=True)
    
    for source_root in SOURCE_DIRS:
        source_root_path = Path(source_root)
        if not source_root_path.exists():
            print(f"Source not found: {source_root}")
            continue
            
        print(f"Processing source: {source_root}")
        
        for root, dirs, files in os.walk(source_root):
            rel_dir = Path(root).relative_to(source_root_path)
            target_dir = Path(TARGET_ROOT) / rel_dir
            
            # Map stems to original images
            originals = {}
            for f in files:
                p = Path(f)
                if p.suffix.lower() in ['.jpg', '.png', '.jpeg']:
                    # Simple heuristic: if it doesn't match result/analysis, it's original
                    if not RE_ANALYSIS.match(f) and not RE_RESULT.match(f) and not RE_RESULT_DOUBLE_UNDER.match(f):
                        originals[p.stem] = f
            
            for f in files:
                source_file = Path(root) / f
                
                # Case 1: Analysis Text
                m = RE_ANALYSIS.match(f)
                if m:
                    stem, seed = m.groups()
                    target_filename = f"{stem}_{seed}_analysis.txt"
                    os.makedirs(target_dir, exist_ok=True)
                    shutil.copy2(source_file, target_dir / target_filename)
                    continue
                
                # Case 2: Result Image or JSON
                m = RE_RESULT.match(f) or RE_RESULT_DOUBLE_UNDER.match(f)
                if m:
                    # Result Double Under has groups: stem, pN, seed, ext
                    # Result has groups: stem, pN, theme_junk, seed, ext
                    groups = m.groups()
                    if len(groups) == 4:
                        stem, p_num, seed, ext = groups
                        theme_junk = ""
                    else:
                        stem, p_num, theme_junk, seed, ext = groups
                    
                    target_filename = f"{stem}_p{p_num}_{seed}.{ext.lower()}"
                    os.makedirs(target_dir, exist_ok=True)
                    
                    if ext.lower() == 'json':
                        # Transform JSON
                        try:
                            with open(source_file, 'r', encoding='utf-8') as jf:
                                data = json.load(jf)
                        except Exception as e:
                            print(f"Error reading JSON {source_file}: {e}")
                            # Try to copy as-is if corrupted or fix manually? 
                            # For now, skip transformation if failed load.
                            shutil.copy2(source_file, target_dir / target_filename)
                            continue
                            
                        # Determine new paths
                        new_target_file = (target_dir / target_filename).absolute()
                        new_gen_img = (target_dir / f"{stem}_p{p_num}_{seed}.jpg").absolute()
                        
                        orig_file_name = originals.get(stem)
                        new_orig_img = (target_dir / orig_file_name).absolute() if orig_file_name else ""
                        
                        std_json = standardize_json(data, new_orig_img, new_gen_img)
                        
                        with open(target_dir / target_filename, 'w', encoding='utf-8') as wj:
                            json.dump(std_json, wj, ensure_ascii=False, indent=2)
                    else:
                        # Just copy JPG
                        shutil.copy2(source_file, target_dir / target_filename)
                    continue
                
                # Case 3: Original image (already handled for grouping, now copy it)
                p = Path(f)
                if p.stem in originals and originals[p.stem] == f:
                    os.makedirs(target_dir, exist_ok=True)
                    shutil.copy2(source_file, target_dir / f)

if __name__ == "__main__":
    process()
    print("Reorganization complete!")
