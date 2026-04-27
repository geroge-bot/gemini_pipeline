import os
import argparse
from pathlib import Path
from collections import defaultdict

def is_image(file_path):
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.heic', '.heif'}
    return file_path.suffix.lower() in image_extensions

def count_images_recursive(path):
    count = 0
    try:
        if path.is_dir():
            for file in path.rglob('*'):
                if file.is_file() and is_image(file):
                    count += 1
    except PermissionError:
        pass
    return count

def main():
    parser = argparse.ArgumentParser(description="Recursively count images in a folder and show breakdown for up to 2 levels of subfolders.")
    parser.add_argument("input_folder", type=str, help="Path to the folder to scan.")
    args = parser.parse_args()

    root_path = Path(args.input_folder)
    if not root_path.exists() or not root_path.is_dir():
        print(f"❌ Error: {args.input_folder} is not a valid directory.")
        return

    print(f"\n🔍 Scanning directory: {root_path.absolute()}")
    
    # Total count
    total_count = count_images_recursive(root_path)
    print(f"📊 Total images found: {total_count}")
    print("=" * 50)

    # Level 1 subfolders
    try:
        level1_dirs = sorted([d for d in root_path.iterdir() if d.is_dir()])
    except PermissionError:
        print("❌ Permission denied reading Level 1 directories.")
        return
    
    for l1_dir in level1_dirs:
        l1_count = count_images_recursive(l1_dir)
        print(f"📁 [L1] {l1_dir.name:<30} | {l1_count:>5} images")
        
        # # Level 2 subfolders
        # try:
        #     level2_dirs = sorted([d for d in l1_dir.iterdir() if d.is_dir()])
        #     for l2_dir in level2_dirs:
        #         l2_count = count_images_recursive(l2_dir)
        #         print(f"  └── 📁 [L2] {l2_dir.name:<25} | {l2_count:>5} images")
        # except PermissionError:
        #     print(f"  └── ❌ Permission denied reading subfolders of {l1_dir.name}")

    print("=" * 50)
    print("Done.\n")

if __name__ == "__main__":
    main()
