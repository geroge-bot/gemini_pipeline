import os
import shutil
import random
from pathlib import Path

# Configuration
SOURCE_DIR = Path(r"D:\美食_标准格式\原图\咖啡厅")
EXCLUDE_DIRS = [
    Path(r"F:\result_1000_NoSeka\咖啡厅2"),
    Path(r"F:\result_1000_NoSeka\咖啡厅")
]
TARGET_DIR = Path(r"D:\美食_标准格式_tmp\咖啡厅")

def get_stems(dir_paths):
    stems = set()
    for d in dir_paths:
        if not d.exists():
            print(f"Warning: Exclude directory not found: {d}")
            continue
        print(f"Collecting exclusion stems from {d}...")
        for root, _, files in os.walk(d):
            for f in files:
                # We use the part of the filename before any _p1_, _analysis_, etc.
                # But since these are results, they might have various suffixes.
                # If the user means "don't include images we already have results for", 
                # then we should match the base stem.
                stem = f.split("_p")[0].split("_analysis")[0].split(".")[0]
                stems.add(stem)
    return stems

def process():
    os.makedirs(TARGET_DIR, exist_ok=True)
    
    # 1. Collect excluded stems
    excluded_stems = get_stems(EXCLUDE_DIRS)
    print(f"Total excluded unique stems: {len(excluded_stems)}")
    
    # 2. Collect source images
    if not SOURCE_DIR.exists():
        print(f"Source directory not found: {SOURCE_DIR}")
        return
        
    print(f"Scanning source images in {SOURCE_DIR}...")
    candidate_images = []
    for root, _, files in os.walk(SOURCE_DIR):
        for f in files:
            if Path(f).suffix.lower() in ['.jpg', '.png', '.jpeg']:
                full_path = Path(root) / f
                stem = full_path.stem
                if stem not in excluded_stems:
                    candidate_images.append(full_path)
    
    print(f"Found {len(candidate_images)} candidate images after exclusion.")
    
    # 3. Sample 500
    if len(candidate_images) < 500:
        print(f"Warning: Only {len(candidate_images)} candidates found, which is less than 500. Sampling all.")
        sampled = candidate_images
    else:
        sampled = random.sample(candidate_images, 500)
    
    # 4. Copy
    print(f"Copying {len(sampled)} images to {TARGET_DIR}...")
    for img_path in sampled:
        # Maintain subfolder structure relative to SOURCE_DIR
        rel_path = img_path.relative_to(SOURCE_DIR)
        target_path = TARGET_DIR / rel_path
        
        os.makedirs(target_path.parent, exist_ok=True)
        shutil.copy2(img_path, target_path)

    print("Sampling and copy complete!")

if __name__ == "__main__":
    process()
