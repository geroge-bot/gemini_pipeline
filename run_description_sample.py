"""
Standalone script: sample 20 (original, generated) image pairs,
run DescriptionModule to produce conversation histories,
and save them to the prompt output directory.
"""
import os
import re
import json
import random
import time

from pipeline.config import DEFAULT_API_KEY, DEFAULT_API_BASE_URL, DEFAULT_MODEL_ANALYSIS
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.file_ops import image_to_base64
from pipeline.modules.description import (
    DescriptionModule,
    PERSONAS,
    GENERAL_QUESTIONS,
    _build_question_prompt,
    _build_answer_prompt,
    _strip_images_from_messages,
)
from pipeline.utils.client_factory import create_client_from_service

# ====================== Configuration ======================
ORIG_DIR = r"D:\美食测试集\美食 - 测试数据\20260330-美食-测试集"
GEN_DIR  = r"D:\美食测试集\美食 - 测试数据\20260330-美食-测试集_output"
OUT_DIR  = r"D:\美食测试集\美食 - 测试数据\20260330-美食-测试集_prompt"
SAMPLE_N = 20
MODEL    = "gemini-3-pro-preview"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def find_pairs(orig_dir: str, gen_dir: str):
    """
    Scan both directories and build a list of (orig_path, gen_path) pairs.
    Generated files follow the pattern: {orig_stem}_p{N}_{theme}_{seed}.jpg
    We pick only the first generated variant (p1) per original to keep it 1:1.
    """
    # Collect all generated image basenames
    gen_files = [
        f for f in os.listdir(gen_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    ]

    # Collect original image stems
    orig_images = [
        f for f in os.listdir(orig_dir)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    ]

    pairs = []
    for orig_name in orig_images:
        orig_stem = os.path.splitext(orig_name)[0]
        # Find generated images that match this original (prefer p1)
        # Pattern: {orig_stem}_p1_{anything}.jpg
        for gf in gen_files:
            if gf.startswith(orig_stem + "_p1_"):
                pairs.append((
                    os.path.join(orig_dir, orig_name),
                    os.path.join(gen_dir, gf),
                ))
                break  # one per original

    return pairs


def run_description(client, model, orig_path, gen_path):
    """
    Run the two-step description flow for a single image pair.
    Returns a dict with persona, question, answer, and conversation_history.
    """
    orig_b64 = image_to_base64(orig_path)
    gen_b64  = image_to_base64(gen_path)

    # ===== Step 1: Construct question =====
    if random.random() < 0.3:
        question = random.choice(GENERAL_QUESTIONS)
        persona  = ""
        step1_messages = []
    else:
        persona = random.choice(PERSONAS)
        step1_messages = _build_question_prompt(persona, orig_b64, gen_b64)
        question = client.generate_with_messages(
            messages=step1_messages, model=model
        ).strip()

    # ===== Step 2: Generate answer =====
    step2_messages = _build_answer_prompt(persona, question, orig_b64, gen_b64)
    answer = client.generate_with_messages(
        messages=step2_messages, model=model
    )

    # ===== Assemble conversation history (images stripped) =====
    full_history = []
    if step1_messages:
        full_history.extend(_strip_images_from_messages(step1_messages))
        full_history.append({"role": "assistant", "content": question})
    full_history.extend(_strip_images_from_messages(step2_messages))
    full_history.append({"role": "assistant", "content": answer})

    return {
        "original_image": os.path.basename(orig_path),
        "generated_image": os.path.basename(gen_path),
        "persona": persona,
        "question": question,
        "answer": answer,
        "conversation_history": full_history,
    }


def main():
    random.seed(42)

    # 1. Find all valid (original, generated) pairs
    print(f"Scanning pairs in:\n  ORIG: {ORIG_DIR}\n  GEN:  {GEN_DIR}")
    all_pairs = find_pairs(ORIG_DIR, GEN_DIR)
    print(f"Found {len(all_pairs)} matchable pairs.")

    if len(all_pairs) < SAMPLE_N:
        print(f"[WARN] Only {len(all_pairs)} pairs available, using all.")
        sampled = all_pairs
    else:
        sampled = random.sample(all_pairs, SAMPLE_N)

    print(f"Sampled {len(sampled)} pairs for description generation.\n")

    # 2. Create output directory
    os.makedirs(OUT_DIR, exist_ok=True)

    # 3. Initialize API client
    client = create_client_from_service(service_name="az_text")

    # 4. Process each pair
    for idx, (orig_path, gen_path) in enumerate(sampled, 1):
        orig_name = os.path.basename(orig_path)
        gen_name  = os.path.basename(gen_path)
        print(f"[{idx}/{len(sampled)}] {orig_name}")
        print(f"         -> {gen_name}")

        # Check if output already exists (skip/resume)
        out_stem = os.path.splitext(gen_name)[0]
        out_json = os.path.join(OUT_DIR, f"{out_stem}_desc.json")
        if os.path.isfile(out_json):
            print(f"  -> [SKIP] Already exists: {os.path.basename(out_json)}\n")
            continue

        try:
            if idx > 1:
                time.sleep(5)  # delay between API calls to avoid rate limits
            result = run_description(client, MODEL, orig_path, gen_path)
            print(f"  -> Question: {result['question']}")
            print(f"  -> Persona:  {result['persona'] or '(general)'}")

            # Save
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"  -> Saved: {os.path.basename(out_json)}\n")

        except Exception as e:
            print(f"  -> [ERROR] {e}\n")

    print(f"\nDone! Results saved to: {OUT_DIR}")


if __name__ == "__main__":
    main()
