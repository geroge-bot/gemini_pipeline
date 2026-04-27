import os
import json
import shutil
from pathlib import Path

# Configuration
SOURCE_ROOT = Path(r"D:\美食_标准格式")
AIGC_SRC = SOURCE_ROOT / "aigc_0320"
SHARE_ROOT = Path(r"D:\美食_标准格式_分享")
SHARE_ORIG = SHARE_ROOT / "原图"
SHARE_TARGET = SHARE_ROOT / "目标图"

def process():
    if not AIGC_SRC.exists():
        print(f"Source results not found: {AIGC_SRC}")
        return
        
    os.makedirs(SHARE_ORIG, exist_ok=True)
    os.makedirs(SHARE_TARGET, exist_ok=True)
    
    annotations = []
    
    print(f"Scanning JSONs in {AIGC_SRC}...")
    
    for root, dirs, files in os.walk(AIGC_SRC):
        root_path = Path(root)
        rel_dir = root_path.relative_to(AIGC_SRC)
        
        for f in files:
            if f.lower().endswith(".json"):
                json_path = root_path / f
                
                try:
                    with open(json_path, 'r', encoding='utf-8') as jf:
                        data = json.load(jf)
                    
                    short_plan = data.get("short_plan")
                    if short_plan and str(short_plan).strip() and str(short_plan).lower() != "null":
                        # Valid record!
                        orig_rel_path = data.get("original_image_path")
                        gen_rel_path = data.get("generated_image_path")
                        
                        if not orig_rel_path or not gen_rel_path:
                            continue
                            
                        # Source absolute paths
                        src_orig_abs = SOURCE_ROOT / orig_rel_path
                        src_gen_abs = SOURCE_ROOT / gen_rel_path
                        
                        if not src_orig_abs.exists() or not src_gen_abs.exists():
                            print(f"Warning: Image not found for {f}")
                            continue
                            
                        # Destination paths
                        # We want SHARE_ROOT/原图/rel_dir/filename
                        # And SHARE_ROOT/目标图/rel_dir/filename
                        dest_orig_abs = SHARE_ORIG / rel_dir / src_orig_abs.name
                        dest_gen_abs = SHARE_TARGET / rel_dir / src_gen_abs.name
                        
                        os.makedirs(dest_orig_abs.parent, exist_ok=True)
                        os.makedirs(dest_gen_abs.parent, exist_ok=True)
                        
                        # Copy images
                        shutil.copy2(src_orig_abs, dest_orig_abs)
                        shutil.copy2(src_gen_abs, dest_gen_abs)
                        
                        # Record annotation
                        # user asked for "原始图路径、目标图路径" in the json
                        # typically these are relative to the share root
                        annotations.append({
                            "original": f"原图/{rel_dir.as_posix()}/{src_orig_abs.name}".replace("//", "/"),
                            "target": f"目标图/{rel_dir.as_posix()}/{src_gen_abs.name}".replace("//", "/"),
                            "prompt": short_plan
                        })
                except Exception as e:
                    print(f"Error processing {json_path}: {e}")

    # Save annotations
    anno_path = SHARE_ROOT / "annotation.json"
    with open(anno_path, 'w', encoding='utf-8') as af:
        json.dump(annotations, af, ensure_ascii=False, indent=2)
        
    print(f"Export complete! Total records: {len(annotations)}")
    print(f"Annotations saved to {anno_path}")

if __name__ == "__main__":
    process()
