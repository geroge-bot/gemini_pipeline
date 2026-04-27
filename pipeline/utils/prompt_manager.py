import os
import re

PROMTS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.md")

class PromptManager:
    """
    Manages loading and saving prompt templates from a Markdown file to maintain a history
    and allow easy editing.
    """
    
    @staticmethod
    def _ensure_file_exists():
        if not os.path.exists(PROMTS_FILE_PATH):
            with open(PROMTS_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write("# Pipeline Prompts\n\n## Analysis Prompts\n\n## Shorten Prompts\n")

    @staticmethod
    def get_all_prompts() -> dict:
        """
        Parses the Markdown file and extracts prompts into categories.
        Returns a dict: {'Analysis Prompts': {'Name': 'content'}, 'Shorten Prompts': {...}}
        """
        PromptManager._ensure_file_exists()
        prompts = {"Analysis Prompts": {}, "Shorten Prompts": {}, "Validation Prompts": {}}
        
        with open(PROMTS_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Very basic markdown heading parser
        current_category = None
        current_prompt_name = None
        current_prompt_content = []
        
        for line in content.split('\n'):
            if line.startswith('## '):
                # Save previous
                if current_category and current_prompt_name and current_prompt_content:
                    prompts[current_category][current_prompt_name] = '\n'.join(current_prompt_content).strip()
                    
                cat_name = line[3:].strip()
                if cat_name not in prompts:
                    prompts[cat_name] = {}
                current_category = cat_name
                current_prompt_name = None
                current_prompt_content = []
            elif line.startswith('### ') and current_category:
                # Save previous
                if current_prompt_name and current_prompt_content:
                    prompts[current_category][current_prompt_name] = '\n'.join(current_prompt_content).strip()
                    
                current_prompt_name = line[4:].strip()
                current_prompt_content = []
            elif current_prompt_name:
                # Exclude markdown codeblock ticks if they wrap the prompt
                if line.strip() != '```' and not line.startswith('```text'):
                    current_prompt_content.append(line)
                
        # Save last one
        if current_category and current_prompt_name and current_prompt_content:
            prompts[current_category][current_prompt_name] = '\n'.join(current_prompt_content).strip()
            
        return prompts

    @staticmethod
    def save_prompt(category: str, name: str, content: str):
        """Saves a new prompt or overwrites an existing one in the markdown file."""
        prompts = PromptManager.get_all_prompts()
        
        if category not in prompts:
            prompts[category] = {}
            
        prompts[category][name] = content
        
        # Rewrite the entire file
        with open(PROMTS_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write("# Pipeline Prompts\n\nThese prompts are generated and managed by the Web UI.\n\n")
            
            for cat, promp_dict in prompts.items():
                f.write(f"## {cat}\n\n")
                for p_name, p_content in promp_dict.items():
                    f.write(f"### {p_name}\n```text\n{p_content}\n```\n\n")
