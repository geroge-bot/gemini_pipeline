import os
import base64
import re
import hashlib
from typing import Optional


def deterministic_seed(random_seed: int, image_stem: str) -> str:
    """生成基于种子和图片名的确定性4位数字后缀，替代 random.randint"""
    h = hashlib.md5(f"{random_seed}_{image_stem}".encode()).hexdigest()
    return str(int(h[:8], 16) % 10000).zfill(4)


def image_to_base64(file_path: str) -> str:
    """Converts an image file to a base64 encoded string."""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

def base64_to_image(base64_str: str, img_path: str) -> bool:
    """Saves a base64 encoded string to an image file."""
    try:
        # Some endpoints return with data:image prefix
        if base64_str.startswith("data:image"):
            base64_str = base64_str.split(",")[1]
            
        image_data = base64.b64decode(base64_str)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(os.path.abspath(img_path)), exist_ok=True)
        
        with open(img_path, 'wb') as f:
            f.write(image_data)
        return True
    except Exception as e:
        print(f"Error saving image {img_path}: {e}")
        return False

def extract_base64_from_response(response_msg: str) -> Optional[str]:
    """Extracts base64 image data from a Markdown or markdown-like text response."""
    pattern = r'data:image/[^;]+;base64,([^"\')\s]+)'
    match = re.search(pattern, response_msg)
    if match: 
        return match.group(1)
    return None

def find_valid_images(directory: str) -> list[str]:
    """Recursively find all valid image files in a directory."""
    VALID_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
    images = []
    
    if not os.path.exists(directory):
        return images
        
    for root, _, files in os.walk(directory):
        for file in files:
            if os.path.splitext(file)[1].lower() in VALID_EXTS:
                images.append(os.path.abspath(os.path.join(root, file)))
                
    return sorted(images)
