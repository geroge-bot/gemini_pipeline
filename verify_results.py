import json
import os
from pathlib import Path

def verify():
    root = Path(r"D:\美食_标准格式")
    aigc_root = root / "aigc_0320"
    orig_root = root / "原图"
    
    sample_files = list(aigc_root.rglob("Autumn_004_46553_p1_3059.json"))
    if not sample_files:
        print("Sample file not found in aigc_0320!")
        return

    f = sample_files[0]
    print(f"Checking file: {f}")
    
    with open(f, 'r', encoding='utf-8') as jf:
        data = json.load(jf)
    
    orig_rel = data.get('original_image_path')
    gen_rel = data.get('generated_image_path')
    print(f"Original Image (Relative): {orig_rel}")
    print(f"Generated Image (Relative): {gen_rel}")
    
    # Verify paths exist relative to root
    orig_abs = root / orig_rel if orig_rel else None
    gen_abs = root / gen_rel if gen_rel else None
    
    if orig_abs:
        print(f"Original Image Exists at {orig_abs}: {orig_abs.exists()}")
    if gen_abs:
        print(f"Generated Image Exists at {gen_abs}: {gen_abs.exists()}")

    # Check if a non-result image exists in 原图
    orig_img_sample = list(orig_root.rglob("Autumn_004_46553.png"))
    print(f"Sample Original Image in '原图' exists: {len(orig_img_sample) > 0}")

if __name__ == "__main__":
    verify()
