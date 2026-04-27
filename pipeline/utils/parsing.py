import re
from typing import List, Dict

def parse_schemes_universal(text: str) -> List[Dict[str, str]]:
    """
    Robustly parses multiple generation schemes out of a raw LLM response.
    Expects XML wrappers <scheme>...</scheme> 
    """
    schemes = []
    clean_text = text.strip()
    
    # 1. Try to find XML tags first
    xml_matches = re.findall(r'<scheme>(.*?)</scheme>', clean_text, re.DOTALL)
    if xml_matches:
        for i, content in enumerate(xml_matches):
            content = content.strip()
            # Extract header as theme
            theme_match = re.search(r'###\s*方案\d+[:：]\s*(.*?)(?:\n|$)', content)
            if theme_match:
                theme = theme_match.group(1).strip()
            else:
                theme = f"Scheme_{i+1}"
                
            theme = re.sub(r'[\\/*?:"<>|]', "_", theme).strip() # Sanitize filename chars
            schemes.append({"theme": theme, "content": content})
        return schemes

    # 2. Fallback: Parse Markdown Headers
    text_normalized = clean_text.replace('\\n', '\n')
    pattern = r'((?:###\s*)?(?:\*\*)?方案\s*[0-9一二三四]+.*?[：:])'
    parts = re.split(pattern, text_normalized)
    
    if len(parts) < 2:
        # Extreme fallback: treat whole text as one giant scheme
        return [{"theme": "Redraw", "content": clean_text}]
        
    for i in range(1, len(parts), 2):
        header = parts[i]
        body = parts[i+1] if i+1 < len(parts) else ""
        raw_text = header + body
        clean_content = re.sub(r'[\"\'\]\}]+\s*$', '', raw_text.strip()).strip().rstrip(',').rstrip('"')
        
        # Extract title
        title_match = re.search(r"[:：]\s*(.*?)(?:\n|$|\*)", header)
        theme = title_match.group(1).strip() if title_match else f"Scheme_{int(i/2)+1}"
        theme = re.sub(r'[\\/*?:"<>|]', "_", theme).strip()
        
        schemes.append({"theme": theme, "content": clean_content})
        
    return schemes

def clean_xml_markdown(text: str) -> str:
    """Removes trailing markdown formats from AI output."""
    if not isinstance(text, str) or not text.strip():
        return ""
        
    match = re.search(r'```(?:xml|json|markdown)?\s*(.*?)\s*```', text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
        
    cleaned_text = re.sub(r'```(xml|json|markdown)?\s*', '', text, flags=re.IGNORECASE)
    cleaned_text = cleaned_text.replace('```', '')
    
    return cleaned_text.strip()
    
def parse_camera_movement(text: str) -> Dict[str, str]:
    """Attempts to extract explicit camera coordinates from the text using basic keywords."""
    movement = {}
    
    pitch_match = re.search(r'(?:俯仰角|Pitch)[\s:=]*([^,\n\.]+)', text, re.IGNORECASE)
    if pitch_match: movement['pitch'] = pitch_match.group(1).strip()
        
    yaw_match = re.search(r'(?:航向角|偏航角|Yaw)[\s:=]*([^,\n\.]+)', text, re.IGNORECASE)
    if yaw_match: movement['yaw'] = yaw_match.group(1).strip()
        
    dist_match = re.search(r'(?:距离|相机距离|Distance)[\s:=]*([^,\n\.]+)', text, re.IGNORECASE)
    if dist_match: movement['distance'] = dist_match.group(1).strip()
        
    zoom_match = re.search(r'(?:焦段|Zoom)[\s:=]*([^,\n\.]+)', text, re.IGNORECASE)
    if zoom_match: movement['zoom'] = zoom_match.group(1).strip()
    
    return movement
