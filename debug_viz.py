import os
import sys

# Ensure pipeline is accessible
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from pipeline.utils.file_ops import find_valid_images, deterministic_seed

def debug_visualize(input_dir, output_dir, seed_val=42):
    print(f"Scanning input_dir: {input_dir}")
    if not os.path.isdir(input_dir):
        print("ERROR: Input directory not found")
        return

    source_images = find_valid_images(input_dir)
    print(f"Found {len(source_images)} source images.")

    for i, img_path in enumerate(source_images[:5]): # Check first 5
        stem = os.path.splitext(os.path.basename(img_path))[0]
        seed = deterministic_seed(seed_val, stem)
        rel_path = os.path.relpath(img_path, input_dir)
        target_dir = os.path.join(output_dir, os.path.dirname(rel_path))
        
        print(f"\nItem {i}:")
        print(f"  img_path: {img_path}")
        print(f"  stem: {stem}")
        print(f"  seed: {seed}")
        print(f"  target_dir: {target_dir}")
        
        # Check if target_dir exists
        if os.path.isdir(target_dir):
            print(f"  [OK] target_dir exists")
            # Check for JSON
            json_name = f"{stem}_p1_{seed}.json"
            json_path = os.path.join(target_dir, json_name)
            if os.path.isfile(json_path):
                print(f"  [OK] Found JSON: {json_name}")
            else:
                print(f"  [FAIL] JSON not found: {json_name}")
                # List files in target_dir to see what's there
                print(f"  Files in target_dir: {os.listdir(target_dir)[:5]}...")
        else:
            print(f"  [FAIL] target_dir does not exist")

if __name__ == "__main__":
    input_dir = r"D:\result_1000_NoLight_3_standard\原图"
    output_dir = r"D:\result_1000_NoLight_3_standard\标注"
    debug_visualize(input_dir, output_dir)
