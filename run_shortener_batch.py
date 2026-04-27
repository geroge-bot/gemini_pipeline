"""
Standalone batch shortener script for 咖啡厅_output.

Scans D:\咖啡厅_output for existing analysis .txt + generated .jpg files,
reconstructs PipelineResult objects, and runs the ShortenModule (mode C)
to generate JSON result files as described in FILE_FORMAT.md.

Usage:
    python run_shortener_batch.py [--max-workers 5] [--sample 0] [--dry-run]
"""

import os
import re
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.models import PipelineConfig, PipelineResult
from pipeline.utils.parsing import parse_schemes_universal, clean_xml_markdown, parse_camera_movement
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import image_to_base64
from pipeline.config import (
    DEFAULT_API_KEY, DEFAULT_API_BASE_URL, DEFAULT_MODEL_SHORTEN,
)

# ========================== Configuration ==========================

INPUT_DIR = r"D:\咖啡厅"           # 原始 .png 源图
OUTPUT_DIR = r"D:\咖啡厅_output"   # analysis .txt + generated .jpg 所在目录

# Load PROMPT_SYS_SHORTEN_C from prompts.md
def load_shorten_prompt_c() -> str:
    prompts_path = os.path.join(os.path.dirname(__file__), "pipeline", "utils", "prompts.md")
    with open(prompts_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # Extract PROMPT_SYS_SHORTEN_C block
    match = re.search(
        r'###\s*PROMPT_SYS_SHORTEN_C\s*\n```text\n(.*?)```',
        content, re.DOTALL
    )
    if not match:
        raise RuntimeError("Could not find PROMPT_SYS_SHORTEN_C in prompts.md")
    return match.group(1).strip()

# ========================== Core Logic ==========================

def find_analysis_files(cafe_output_dir: str) -> list[str]:
    """Find all *_analysis_*.txt files in a café output directory."""
    results = []
    for f in os.listdir(cafe_output_dir):
        if '_analysis_' in f and f.endswith('.txt'):
            results.append(os.path.join(cafe_output_dir, f))
    return sorted(results)


def find_generated_images_for_stem(cafe_output_dir: str, stem: str, seed: str) -> dict[int, str]:
    """
    Find generated image files matching the pattern {stem}_p{N}_*_{seed}.jpg
    Returns dict mapping scheme_index (1-based) to file path.
    """
    pattern = re.compile(
        rf'^{re.escape(stem)}_p(\d+)_.*?{re.escape(seed)}\.jpg$',
        re.IGNORECASE
    )
    matches = {}
    for f in os.listdir(cafe_output_dir):
        m = pattern.match(f)
        if m:
            idx = int(m.group(1))
            matches[idx] = os.path.join(cafe_output_dir, f)
    return matches


def extract_stem_and_seed_from_analysis(analysis_filename: str):
    """
    Parse analysis filename like 'STEM_SEED_analysis.txt' → (stem, seed)
    The pattern is: {stem}_{seed}_analysis_{something}.txt
    Wait, looking at actual files: 澳白_001_19456_analysis_7362.txt
    So the pattern is: {stem}_analysis_{seed}.txt where stem=澳白_001_19456 and seed=7362?
    No wait, let's check: from the code, the analysis file is named {stem}_{seed}_analysis.txt
    But actual files are like: Autumn_004_46553_analysis_3059.txt
    The stem is Autumn_004_46553 (which is the original PNG filename without extension)
    And the seed is... let me re-examine.
    
    From analysis.py line 24: f"{stem}_{seed}_analysis.txt"
    So for file Autumn_004_46553_analysis_3059.txt:
    This doesn't match {stem}_{seed}_analysis.txt...
    
    Actually wait: the original PNG is Autumn_004_46553.png, so stem = Autumn_004_46553
    And seed would be some value. But the filename is Autumn_004_46553_analysis_3059.txt
    which would mean: stem_analysis_seed.txt  i.e. {stem}_analysis_{seed}.txt ?
    
    No, looking at the code: f"{stem}_{seed}_analysis.txt"
    stem = Autumn_004_46553, seed = ?
    Result: Autumn_004_46553_{seed}_analysis.txt
    
    But actual file: Autumn_004_46553_analysis_3059.txt
    Hmm, this doesn't match. The actual pattern seems to be {stem}_analysis_{seed}.txt
    
    Let me just use regex to parse it.
    """
    # Match pattern: (.+)_analysis_(\d+)\.txt
    m = re.match(r'^(.+)_analysis_(\d+)\.txt$', analysis_filename)
    if m:
        return m.group(1), m.group(2)
    
    # Fallback: (.+)_(\d+)_analysis\.txt  (from code pattern)
    m = re.match(r'^(.+)_(\d+)_analysis\.txt$', analysis_filename)
    if m:
        return m.group(1), m.group(2)
    
    return None, None


def find_original_image(input_cafe_dir: str, stem: str) -> str | None:
    """Find the original .png image in the input directory matching the stem."""
    candidates = [f for f in os.listdir(input_cafe_dir) if os.path.splitext(f)[0] == stem]
    if candidates:
        return os.path.join(input_cafe_dir, candidates[0])
    return None


def process_one_analysis_file(
    analysis_path: str,
    cafe_output_dir: str,
    cafe_input_dir: str,
    prompt_template: str,
    client: GeminiAPIClient,
    dry_run: bool = False,
) -> dict:
    """
    Process a single analysis file:
    - Parse schemes from the analysis text
    - Match each to its generated .jpg
    - Call shortener API (mode C) for each scheme
    - Save JSON
    Returns stats dict.
    """
    analysis_filename = os.path.basename(analysis_path)
    stem, seed = extract_stem_and_seed_from_analysis(analysis_filename)
    
    if not stem or not seed:
        print(f"  [WARN] Could not parse stem/seed from {analysis_filename}")
        return {"skipped": 0, "processed": 0, "failed": 0, "error": 1}
    
    # Read analysis text
    with open(analysis_path, 'r', encoding='utf-8') as f:
        analysis_text = f.read().strip()
    
    if not analysis_text:
        return {"skipped": 0, "processed": 0, "failed": 0, "error": 1}
    
    # Parse schemes
    schemes = parse_schemes_universal(analysis_text)
    if not schemes:
        print(f"  [WARN] No schemes found in {analysis_filename}")
        return {"skipped": 0, "processed": 0, "failed": 0, "error": 1}
    
    # Find generated images
    gen_images = find_generated_images_for_stem(cafe_output_dir, stem, seed)
    
    # Find original image
    original_image_path = find_original_image(cafe_input_dir, stem) or ""
    
    stats = {"skipped": 0, "processed": 0, "failed": 0, "error": 0}
    
    for i, scheme in enumerate(schemes[:4]):
        scheme_idx = i + 1
        gen_img_path = gen_images.get(scheme_idx)
        
        if not gen_img_path:
            # No generated image for this scheme
            continue
        
        # Check if JSON already exists with short_plan
        json_path = os.path.splitext(gen_img_path)[0] + ".json"
        if os.path.isfile(json_path):
            try:
                import json
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get("short_plan", "").strip():
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass
        
        if dry_run:
            print(f"  [DRY-RUN] Would process: {os.path.basename(gen_img_path)}")
            stats["processed"] += 1
            continue
        
        # Build PipelineResult
        result = PipelineResult(
            theme=scheme['theme'],
            original_image_path=original_image_path,
            generated_image_path=gen_img_path,
            original_plan=scheme['content'],
            analysis_prompt_used="",  # not available in standalone mode
            mode="C",
            status="generated",
            camera_movement=parse_camera_movement(scheme['content']),
        )
        
        # Call shortener API (mode C: send generated image)
        try:
            prompt = prompt_template.format(suggestion=result.original_plan)
            gen_b64 = image_to_base64(gen_img_path)
            
            # Mode C: send both original image and generated image
            # Actually looking at the shortener code, mode C sends *generated* image
            # along with the text prompt containing the original plan
            short_plan_raw = client.generate_text(
                prompt=prompt,
                image_base64=gen_b64,
                model=DEFAULT_MODEL_SHORTEN,
            )
            
            result.short_plan = clean_xml_markdown(short_plan_raw)
            result.shorten_prompt_used = prompt
            result.status = "success"
            
            # Save JSON
            saved = result.save_json(cafe_output_dir)
            print(f"  -> Saved: {os.path.basename(saved)}")
            stats["processed"] += 1
            
        except Exception as e:
            result.status = "failed"
            result.error_message = f"Shorten failed: {str(e)}"
            # Still save JSON with error status
            try:
                result.save_json(cafe_output_dir)
            except Exception:
                pass
            print(f"  -> [ERROR] {os.path.basename(gen_img_path)}: {e}")
            stats["failed"] += 1
    
    return stats


def process_one_cafe(
    cafe_name: str,
    prompt_template: str,
    client: GeminiAPIClient,
    dry_run: bool = False,
) -> dict:
    """Process all analysis files in a single café directory."""
    cafe_output_dir = os.path.join(OUTPUT_DIR, cafe_name)
    cafe_input_dir = os.path.join(INPUT_DIR, cafe_name)
    
    if not os.path.isdir(cafe_output_dir):
        return {"skipped": 0, "processed": 0, "failed": 0, "error": 0}
    
    analysis_files = find_analysis_files(cafe_output_dir)
    
    cafe_stats = {"skipped": 0, "processed": 0, "failed": 0, "error": 0}
    
    for af in analysis_files:
        s = process_one_analysis_file(
            af, cafe_output_dir, cafe_input_dir,
            prompt_template, client, dry_run
        )
        for k in cafe_stats:
            cafe_stats[k] += s[k]
    
    return cafe_stats


def main():
    parser = argparse.ArgumentParser(description="Batch shortener for 咖啡厅_output")
    parser.add_argument("--max-workers", type=int, default=5, help="Concurrent workers")
    parser.add_argument("--sample", type=int, default=0, help="Limit to N cafés (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Don't call API, just show what would be processed")
    args = parser.parse_args()
    
    print("=" * 60)
    print("[START] Batch Shortener - 咖啡厅_output")
    print(f"[DIR] Input: {INPUT_DIR}")
    print(f"[DIR] Output: {OUTPUT_DIR}")
    print(f"[WORKERS] {args.max_workers}")
    print("=" * 60)
    
    # Load prompt
    prompt_template = load_shorten_prompt_c()
    print(f"[PROMPT] Loaded PROMPT_SYS_SHORTEN_C ({len(prompt_template)} chars)")
    
    # List all café directories
    cafes = sorted([
        d for d in os.listdir(OUTPUT_DIR)
        if os.path.isdir(os.path.join(OUTPUT_DIR, d))
    ])
    
    if args.sample > 0:
        cafes = cafes[:args.sample]
    
    print(f"[INFO] Found {len(cafes)} café directories to process\n")
    
    total_stats = {"skipped": 0, "processed": 0, "failed": 0, "error": 0}
    start_time = time.time()
    
    # Create one API client per worker thread (each needs its own)
    def worker(cafe_name):
        client = GeminiAPIClient(api_key=DEFAULT_API_KEY, base_url=DEFAULT_API_BASE_URL)
        print(f"\n[{cafe_name}] Processing...")
        return cafe_name, process_one_cafe(cafe_name, prompt_template, client, args.dry_run)
    
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {executor.submit(worker, c): c for c in cafes}
        
        for future in as_completed(futures):
            try:
                cafe_name, stats = future.result()
                for k in total_stats:
                    total_stats[k] += stats[k]
                summary = ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0)
                if summary:
                    print(f"[{cafe_name}] Done: {summary}")
            except Exception as e:
                cafe_name = futures[future]
                print(f"[{cafe_name}] FATAL ERROR: {e}")
                total_stats["error"] += 1
    
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"[FINISH] Batch Shortener completed in {elapsed:.1f}s")
    print(f"  Processed: {total_stats['processed']}")
    print(f"  Skipped (already done): {total_stats['skipped']}")
    print(f"  Failed: {total_stats['failed']}")
    print(f"  Errors: {total_stats['error']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
