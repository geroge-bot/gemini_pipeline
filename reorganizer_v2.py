import os
import json
import shutil
import re
from pathlib import Path

# Configuration
ROOT = Path(r"D:\美食_标准格式")
ORIG_ROOT = ROOT / "原图"
AIGC_ROOT = ROOT / "aigc_0320"

# Patterns (same as before)
RE_ANALYSIS = re.compile(r"^(.*)_(\d{4})_analysis\.txt$", re.IGNORECASE)
RE_RESULT = re.compile(r"^(.*)_p(\d)_(\d{4})\.(jpg|json)$", re.IGNORECASE)

def process():
    # We walk the ROOT, but we must avoid recursion into the new folders if they already exist
    # Or better, we collect all files first to avoid moving things multiple times or into himself.
    
    file_queue = []
    print(f"Scanning files in {ROOT}...")
    
    for root, dirs, files in os.walk(ROOT):
        root_path = Path(root)
        # Skip the newly created folders
        if ORIG_ROOT in root_path.parents or root_path == ORIG_ROOT:
            continue
        if AIGC_ROOT in root_path.parents or root_path == AIGC_ROOT:
            continue
            
        rel_dir = root_path.relative_to(ROOT)
        for f in files:
            file_queue.append((root_path / f, rel_dir, f))

    print(f"Collected {len(file_queue)} files. Starting move and update...")

    # First pass: Move files and group them to help JSON update
    # We need to know where the original image went to update the JSON
    for src_path, rel_dir, filename in file_queue:
        is_result = RE_RESULT.match(filename) or RE_ANALYSIS.match(filename)
        
        if is_result:
            target_base = AIGC_ROOT
        elif Path(filename).suffix.lower() in ['.jpg', '.png', '.jpeg']:
            target_base = ORIG_ROOT
        else:
            # Skip non-image/non-result files if any (like .py scripts)
            continue
            
        target_dir = target_base / rel_dir
        os.makedirs(target_dir, exist_ok=True)
        target_path = target_dir / filename
        
        # If target exists, maybe log and skip?
        if target_path.exists():
             print(f"Warning: Target exists, skipping: {target_path}")
             continue
             
        shutil.move(src_path, target_path)

    # Second pass: Update JSONs in AIGC_ROOT
    print("Updating JSON files with relative paths...")
    for root, dirs, files in os.walk(AIGC_ROOT):
        root_path = Path(root)
        rel_dir = root_path.relative_to(AIGC_ROOT)
        
        for f in files:
            if f.lower().endswith(".json"):
                json_path = root_path / f
                m = RE_RESULT.match(f)
                if m:
                    stem, p_num, seed, ext = m.groups()
                    
                    try:
                        with open(json_path, 'r', encoding='utf-8') as jf:
                            data = json.load(jf)
                        
                        # Calculate relative paths
                        # 1. Original Image Path
                        # We need to find the original extension. 
                        # It should be in ORIG_ROOT / rel_dir / stem.*
                        orig_search_dir = ORIG_ROOT / rel_dir
                        found_orig = None
                        if orig_search_dir.exists():
                            for ext in ['.png', '.jpg', '.jpeg']:
                                if (orig_search_dir / (stem + ext)).exists():
                                    found_orig = f"原图/{rel_dir.as_posix()}/{stem + ext}".replace("//", "/")
                                    break
                        
                        # 2. Generated Image Path
                        gen_img_name = f"{stem}_p{p_num}_{seed}.jpg"
                        gen_rel_path = f"aigc_0320/{rel_dir.as_posix()}/{gen_img_name}".replace("//", "/")
                        
                        data['original_image_path'] = found_orig
                        data['generated_image_path'] = gen_rel_path
                        
                        with open(json_path, 'w', encoding='utf-8') as wj:
                            json.dump(data, wj, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"Error updating JSON {json_path}: {e}")

if __name__ == "__main__":
    process()
    print("Phase 2 Reorganization complete!")
