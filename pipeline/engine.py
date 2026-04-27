import os
import time
import threading
from typing import List, Optional, Type
from concurrent.futures import ThreadPoolExecutor, as_completed
from pipeline.interfaces import PipelineModule, PipelineContext
from pipeline.models import PipelineConfig
from pipeline.utils.file_ops import find_valid_images

class PipelineEngine:
    """
    Orchestrates the execution of pipeline modules across a directory of images.
    Supports concurrency, robust error tracking, and graceful cancellation.
    """
    def __init__(self, config: PipelineConfig, cancel_event: Optional[threading.Event] = None):
        self.config = config
        self.modules: List[PipelineModule] = []
        self.cancel_event = cancel_event
        
    def add_module(self, module: PipelineModule):
        """Registers a module to the pipeline sequence."""
        self.modules.append(module)
        return self

    def _is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _process_single_image(self, image_path: str) -> PipelineContext:
        """Runs the registered sequence of modules on a single image."""
        context = PipelineContext(config=self.config, original_image_path=image_path)
        
        for module in self.modules:
            # Check cancellation before each module
            if self._is_cancelled():
                print(f"[CANCEL] Skipping {module.__class__.__name__} for {os.path.basename(image_path)} — stopped by user.")
                break
            try:
                context = module.process(context)
            except Exception as e:
                print(f"[Engine] Fatal error processing {os.path.basename(image_path)} in {module.__class__.__name__}: {str(e)}")
                # Stop processing this image and move to the next
                break
                
        return context

    def run(self):
        """Executes the pipeline on all valid images in the input directory concurrently."""
        print(f"==================================================")
        print(f"[START] Starting Pipeline Engine")
        print(f"[DIR] Input: {self.config.input_directory}")
        print(f"[DIR] Output: {self.config.output_directory}")
        print(f"==================================================")
        
        target_images = find_valid_images(self.config.input_directory)
        if not target_images:
            print("[WARN] No valid images found in the input directory.")
            return []
            
        if self.config.sample_size > 0:
            target_images = target_images[:self.config.sample_size]
            print(f"[INFO] Sample size specified. Limiting to {self.config.sample_size} images.")
            
        print(f"Found {len(target_images)} images to process. Running with {self.config.max_workers} workers.\n")
        
        results_contexts = []
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_img = {}
            
            for path in target_images:
                # Check cancellation before submitting each image
                if self._is_cancelled():
                    print(f"[CANCEL] Pipeline stopped by user. Skipping remaining images.")
                    break
                future_to_img[executor.submit(self._process_single_image, path)] = path
            
            for future in as_completed(future_to_img):
                img_path = future_to_img[future]
                
                # Check cancellation while collecting results
                if self._is_cancelled():
                    # Cancel any pending futures
                    for f in future_to_img:
                        f.cancel()
                    print(f"[CANCEL] Pipeline stopped by user. Cancelling remaining tasks.")
                    break
                    
                try:
                    ctx = future.result()
                    results_contexts.append(ctx)
                except Exception as exc:
                    print(f"[ERROR] Unhandled Engine Exception on {img_path}: {exc}")
                    
        elapsed = time.time() - start_time
        
        if self._is_cancelled():
            print(f"\n==================================================")
            print(f"[STOPPED] Pipeline was stopped by user after {elapsed:.2f} seconds.")
            print(f"Processed {len(results_contexts)} of {len(target_images)} source images before stopping.")
            print(f"==================================================")
        else:
            print(f"\n==================================================")
            print(f"[FINISH] Pipeline Finished in {elapsed:.2f} seconds.")
            print(f"Processed {len(target_images)} source images.")
            
            # Calculate gross stats
            total_schemes = sum(len(ctx.results) for ctx in results_contexts)
            success_schemes = sum(1 for ctx in results_contexts for r in ctx.results if r.status == "success")
            
            print(f"[STATS] Total Schemes Generated: {total_schemes}")
            print(f"[STATS] Fully Successful Pipelines: {success_schemes}")
            print(f"==================================================")
        
        return results_contexts
