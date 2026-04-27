import os
import glob
import re
import shutil

def copy_and_rename_files(dir_src, dir_tgt):
    if not os.path.exists(dir_src):
        print(f"源目录不存在，请检查路径: {dir_src}")
        return
        
    if not os.path.exists(dir_tgt):
        os.makedirs(dir_tgt)

    # 递归查找所有文件
    src_files = glob.glob(os.path.join(dir_src, "**", "*.*"), recursive=True)
    
    copied_count = 0
    skipped_count = 0
    
    for filepath in src_files:
        if not os.path.isfile(filepath):
            continue
        if not "_p1"in filepath and not "_p2" in filepath:
            continue

        filename = os.path.basename(filepath)
        
        # 使用正则匹配并替换后缀名之前多余的 _xxx
        # 比如： _p1_5711.jpg -> _p1.jpg
        # _p2_1234.json -> _p2.json
        new_filename = re.sub(r'_p([12])_[^.]+(\.[a-zA-Z0-9]+)$', r'_p\1\2', filename)
        
        # 保持原有的相对目录结构（如果源目录有子文件夹的话）
        rel_path = os.path.relpath(os.path.dirname(filepath), dir_src)
        if rel_path == '.':
            target_dir = dir_tgt
        else:
            target_dir = os.path.join(dir_tgt, rel_path)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
                
        new_filepath = os.path.join(target_dir, new_filename)
        
        # 拷贝并重命名
        if not os.path.exists(new_filepath):
            try:
                shutil.copy2(filepath, new_filepath)
                copied_count += 1
                # print(f"Copied: {filename} -> {new_filename}")
            except Exception as e:
                print(f"拷贝失败 {filename}: {e}")
        else:
            skipped_count += 1
            # print(f"Warning: Target file {new_filename} already exists! Skipping {filename}")

    print(f"\n复制并改名完成! 成功拷贝并重命名了 {copied_count} 个文件.")
    if skipped_count > 0:
        print(f"由于文件已存在而跳过: {skipped_count} 个文件.")

if __name__ == "__main__":
    # TODO: 请您填写真正的源目录路径（您希望从哪里拷贝文件过去）
    DIR_SRC = r"D:\gemini_测试结果\标注"  
    
    DIR_TGT = r"D:\gemini_测试结果\标注_2方案"
    
    copy_and_rename_files(DIR_SRC, DIR_TGT)
