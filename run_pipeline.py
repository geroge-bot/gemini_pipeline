# -*- coding: utf-8 -*-
"""
Simple inference script for the Food Photography Pipeline.
Replicates auto_photographer_food.py functionality using the modular pipeline.

Usage:
    python run_pipeline.py
    python run_pipeline.py -i D:/input -o D:/output -w 3
"""
import argparse

from pipeline.config import (
    DEFAULT_INPUT_DIRECTORY, DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_MAX_WORKERS, DEFAULT_ANALYSIS_PROMPT_KEY, DEFAULT_DRAW_PROMPT_KEY,
    DEFAULT_SHORTEN_PROMPT_KEY, DEFAULT_VALIDATION_PROMPT_KEY, DEFAULT_SHORTEN_MODE, DEFAULT_RANDOM_SEED,
    DEFAULT_MODEL_ANALYSIS, DEFAULT_MODEL_GENERATION, DEFAULT_MODEL_SHORTEN,
)
from pipeline.models import PipelineConfig
from pipeline.engine import PipelineEngine
from pipeline.modules.analysis import AnalysisModule
from pipeline.modules.generation import GenerationModule
from pipeline.modules.shortener import ShortenModule
from pipeline.modules.validator import ValidatorModule
from pipeline.utils.prompt_manager import PromptManager


def main():
    parser = argparse.ArgumentParser(description="Food Photography Pipeline - Inference")
    parser.add_argument("--input", "-i", default=DEFAULT_INPUT_DIRECTORY, help="Input image directory")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT_DIRECTORY, help="Output directory")
    parser.add_argument("--workers", "-w", type=int, default=DEFAULT_MAX_WORKERS, help="Concurrent workers")
    parser.add_argument("--mode", choices=["A", "B", "C"], default=DEFAULT_SHORTEN_MODE, help="Shorten mode")
    parser.add_argument("--seed", "-s", type=int, default=DEFAULT_RANDOM_SEED, help="Random seed for deterministic file naming")
    parser.add_argument("--model-analysis", default=DEFAULT_MODEL_ANALYSIS, help="Model for analysis stage")
    parser.add_argument("--model-generation", default=DEFAULT_MODEL_GENERATION, help="Model for image generation stage")
    parser.add_argument("--model-shorten", default=DEFAULT_MODEL_SHORTEN, help="Model for shorten stage")
    args = parser.parse_args()

    # --- 1. Build config from args + defaults ---
    config = PipelineConfig(
        input_directory=args.input,
        output_directory=args.output,
        max_workers=args.workers,
        shorten_mode=args.mode,
        random_seed=args.seed,
        model_analysis=args.model_analysis,
        model_generation=args.model_generation,
        model_shorten=args.model_shorten,
    )

    # --- 2. Load prompts from prompts.md ---
    prompts = PromptManager.get_all_prompts()
    analysis_prompt = prompts.get("Analysis Prompts", {}).get(DEFAULT_ANALYSIS_PROMPT_KEY, "")
    draw_prompt = prompts.get("Analysis Prompts", {}).get(DEFAULT_DRAW_PROMPT_KEY, "")
    shorten_prompt = prompts.get("Shorten Prompts", {}).get(DEFAULT_SHORTEN_PROMPT_KEY, "")
    validation_prompt = prompts.get("Validation Prompts", {}).get(DEFAULT_VALIDATION_PROMPT_KEY, "")

    if not analysis_prompt:
        print(f"[ERROR] Analysis prompt '{DEFAULT_ANALYSIS_PROMPT_KEY}' not found in prompts.md")
        return
    if not draw_prompt:
        print(f"[ERROR] Draw prompt '{DEFAULT_DRAW_PROMPT_KEY}' not found in prompts.md")
        return
    if not shorten_prompt:
        print(f"[WARN] Shorten prompt '{DEFAULT_SHORTEN_PROMPT_KEY}' not found, shortener may use fallback.")
    if not validation_prompt:
        print(f"[WARN] Validation prompt '{DEFAULT_VALIDATION_PROMPT_KEY}' not found, validator may fail.")

    # --- 3. Assemble and run pipeline ---
    engine = PipelineEngine(config=config)
    engine.add_module(AnalysisModule(prompt_template=analysis_prompt))
    engine.add_module(GenerationModule(prompt_template=draw_prompt))
    engine.add_module(ShortenModule(prompt_template=shorten_prompt))
    if validation_prompt:
        engine.add_module(ValidatorModule(prompt_template=validation_prompt))

    results = engine.run()

    # --- 4. Print summary ---
    total = sum(len(ctx.results) for ctx in results)
    success = sum(1 for ctx in results for r in ctx.results if r.status == "success")
    failed = sum(1 for ctx in results for r in ctx.results if r.status == "failed")

    print(f"\n{'=' * 40}")
    print(f"[Summary] {success} success / {failed} failed / {total} total schemes")
    print(f"{'=' * 40}")

    # Print errors if any
    for ctx in results:
        for r in ctx.results:
            if r.status == "failed" and r.error_message:
                print(f"  [FAIL] {r.theme}: {r.error_message}")


if __name__ == "__main__":
    main()
