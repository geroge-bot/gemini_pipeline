import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import DEFAULT_API_KEY, DEFAULT_API_KEY_IMAGE
from pipeline.models import PipelineConfig
from pipeline.engine import PipelineEngine
from pipeline.modules.analysis import AnalysisModule
from pipeline.modules.generation import GenerationModule
from pipeline.modules.shortener import ShortenModule
from pipeline.utils.prompt_manager import PromptManager

def main():
    config = PipelineConfig(
        input_directory=r"D:\test\food_test",
        output_directory=r"D:\test\food_test_output",
        api_key=DEFAULT_API_KEY,
        api_key_image=DEFAULT_API_KEY_IMAGE,
    )
    
    prompts = PromptManager.get_all_prompts()
    aprompt = prompts.get("Analysis Prompts", {}).get("PROMPT_PLANNER_FOOD_v4", "test")
    dprompt = prompts.get("Analysis Prompts", {}).get("PROMPT_DRAW_IMAGE", "test")
    sprompt = prompts.get("Shorten Prompts", {}).get("PROMPT_SYS_SHORTEN_C", "test")
    
    engine = PipelineEngine(config=config)
    engine.add_module(AnalysisModule(prompt_template=aprompt))
    engine.add_module(GenerationModule(prompt_template=dprompt))
    engine.add_module(ShortenModule(prompt_template=sprompt))
    
    results = engine.run()
    
    # Check results
    success_count = sum(1 for ctx in results for r in ctx.results if r.status == "success")
    print("\n[Final Test Status]")
    print(f"Success Count: {success_count}")
    
    for ctx in results:
        for i, r in enumerate(ctx.results):
            print(f"[{ctx.original_image_path} - Scheme {i+1}] Status: {r.status}")
            if r.error_message:
                print(f"   => Error: {r.error_message}")

if __name__ == "__main__":
    main()
