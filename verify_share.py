import json
import os
from pathlib import Path

def verify():
    root = Path(r"D:\美食_标准格式_分享")
    anno_path = root / "annotation.json"
    
    if not anno_path.exists():
        print("annotation.json not found!")
        return

    with open(anno_path, 'r', encoding='utf-8') as af:
        data = json.load(af)
    
    print(f"Total records in annotation.json: {len(data)}")
    
    if len(data) > 0:
        item = data[0]
        print(f"Sample Item:\n{json.dumps(item, ensure_ascii=False, indent=2)}")
        
        orig_path = root / item['original']
        target_path = root / item['target']
        
        print(f"Original file exists: {orig_path.exists()}")
        print(f"Target file exists: {target_path.exists()}")
        
        # Check folders
        print(f"Originals folder exists: {(root / '原图').exists()}")
        print(f"Targets folder exists: {(root / '目标图').exists()}")

if __name__ == "__main__":
    verify()
