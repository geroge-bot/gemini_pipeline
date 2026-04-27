import os
import glob
import json
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.models import PipelineResult
from pipeline.utils.file_ops import deterministic_seed

class LoaderModule(PipelineModule):
    """
    Loads existing PipelineResults from the output directory into the context.
    Useful when running only later stages (e.g. validate, shorten) without running Analysis.
    """
    def process(self, context: PipelineContext) -> PipelineContext:
        if context.results:
            return context
            
        img_name = os.path.basename(context.original_image_path)
        stem = os.path.splitext(img_name)[0]
        seed = deterministic_seed(context.config.random_seed, stem)
        
        rel_path = os.path.relpath(context.original_image_path, context.config.input_directory)
        target_dir = os.path.join(context.config.output_directory, os.path.dirname(rel_path))
        
        if not os.path.exists(target_dir):
            return context
            
        print(f"[{img_name}] Loading existing results from JSON...")
        loaded_results = []
        
        for n in range(1, 10):
            json_pattern = os.path.join(target_dir, f"{stem}_p{n}_*_{seed}.json")
            json_simple = os.path.join(target_dir, f"{stem}_p{n}_{seed}.json")
            
            json_path = None
            if os.path.isfile(json_simple):
                json_path = json_simple
            else:
                matches = glob.glob(json_pattern)
                if matches:
                    json_path = matches[0]
                    
            if json_path and os.path.isfile(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # Ignore fields not in the model if any, pydantic handles by default
                    result = PipelineResult(**data)
                    loaded_results.append((n, result))
                except Exception as e:
                    print(f"  -> [ERROR] Failed to load {os.path.basename(json_path)}: {e}")
                    
        loaded_results.sort(key=lambda x: x[0])
        context.results = [r for n, r in loaded_results]
        
        if context.results:
            print(f"  -> Loaded {len(context.results)} schemes.")
            
        return context
