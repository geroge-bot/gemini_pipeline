import os
import random
import shutil
from pathlib import Path

def sample_and_copy(src_dir, dst_dir, sample_count=500):
    src_path = Path(src_dir)
    dst_path = Path(dst_dir)
    
    # Get all image files
    image_extensions = {'.png', '.jpg', '.jpeg', '.webp'}
    all_files = []
    for root, dirs, files in os.walk(src_path):
        for file in files:
            if Path(file).suffix.lower() in image_extensions:
                all_files.append(Path(root) / file)
                
    print(f"Found {len(all_files)} images in {src_dir}")
    
    # Sample files
    if len(all_files) <= sample_count:
        sampled_files = all_files
        print(f"Source contains fewer than or equal to {sample_count} files. Copying all.")
    else:
        sampled_files = random.sample(all_files, sample_count)
        print(f"Sampled {sample_count} files.")
        
    # Copy files
    for file_path in sampled_files:
        # Calculate relative path
        rel_path = file_path.relative_to(src_path)
        target_path = dst_path / rel_path
        
        # Create target directory if it doesn't exist
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Copy file
        shutil.copy2(file_path, target_path)
        # print(f"Copied {rel_path}")

    print(f"Finished copying {len(sampled_files)} files to {dst_dir}")

if __name__ == "__main__":
    src = r"D:\美食_标准格式\原图\中餐"
    dst = r"D:\美食_标准格式_tmp\中餐"
    sample_and_copy(src, dst, 500)
