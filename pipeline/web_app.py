from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
import sys
import os
import json
import threading
from threading import Thread, Lock

class TailLogger:
    def __init__(self, max_lines=100):
        self.original_stdout = sys.stdout
        self.max_lines = max_lines
        self.log_lines = []
        self._lock = Lock()
        
    def __getattr__(self, name):
        return getattr(self.original_stdout, name)
    
    def write(self, msg):
        self.original_stdout.write(msg)
        with self._lock:
            if msg.strip():
                for line in msg.strip('\r\n').split('\n'):
                    if line.strip():
                        self.log_lines.append(line.strip())
                if len(self.log_lines) > self.max_lines:
                    self.log_lines = self.log_lines[-self.max_lines:]
                    
    def flush(self):
        self.original_stdout.flush()
        
    def get_logs(self):
        with self._lock:
            return "\n".join(self.log_lines)
            
    def clear(self):
        with self._lock:
            self.log_lines = []

sys.stdout = TailLogger()

# Ensure pipeline is accessible
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.config import (
    DEFAULT_API_KEY, DEFAULT_API_KEY_IMAGE,
    DEFAULT_INPUT_DIRECTORY, DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_SHORTEN_MODE, DEFAULT_MAX_WORKERS, DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLE_SIZE,
    DEFAULT_MODEL_ANALYSIS, DEFAULT_MODEL_GENERATION, DEFAULT_MODEL_SHORTEN,
    DEFAULT_MODEL_ANALYSIS, DEFAULT_MODEL_GENERATION, DEFAULT_MODEL_SHORTEN,
    TEXT_MODEL_CHOICES, IMAGE_MODEL_CHOICES,
    DEFAULT_ANALYSIS_PROMPT_KEY, DEFAULT_SHORTEN_PROMPT_KEY, DEFAULT_VALIDATION_PROMPT_KEY,
)
from pipeline.models import PipelineConfig
from pipeline.engine import PipelineEngine
from pipeline.modules.analysis import AnalysisModule
from pipeline.modules.generation import GenerationModule
from pipeline.modules.shortener import ShortenModule
from pipeline.modules.validator import ValidatorModule
from pipeline.utils.prompt_manager import PromptManager
from pipeline.utils.file_ops import find_valid_images, deterministic_seed

app = Flask(__name__, template_folder='web', static_folder='web')

# Current configured state — all defaults come from config.py
current_config = PipelineConfig(
    input_directory=DEFAULT_INPUT_DIRECTORY,
    output_directory=DEFAULT_OUTPUT_DIRECTORY,
    shorten_mode=DEFAULT_SHORTEN_MODE,
    max_workers=DEFAULT_MAX_WORKERS,
    api_key=DEFAULT_API_KEY,
    api_key_image=DEFAULT_API_KEY_IMAGE,
    random_seed=DEFAULT_RANDOM_SEED,
    sample_size=DEFAULT_SAMPLE_SIZE,
    model_analysis=DEFAULT_MODEL_ANALYSIS,
    model_generation=DEFAULT_MODEL_GENERATION,
    model_shorten=DEFAULT_MODEL_SHORTEN,
)

# Global status tracking
runtime_status = {
    "is_running": False,
    "last_result": None,
    "cancel_event": None
}

def run_pipeline_thread(config: PipelineConfig, analysis_prompt: str, draw_prompt: str, shorten_prompt: str, validation_prompt: str, modules_to_run: list):
    runtime_status["is_running"] = True
    runtime_status["cancel_event"] = threading.Event()
    if hasattr(sys.stdout, 'clear'):
        sys.stdout.clear()
    try:
        engine = PipelineEngine(config=config, cancel_event=runtime_status["cancel_event"])
        
        # Build the sequence conditionally
        if 'analysis' in modules_to_run:
            engine.add_module(AnalysisModule(prompt_template=analysis_prompt))
        else:
            from pipeline.modules.loader import LoaderModule
            engine.add_module(LoaderModule())
            
        if 'generation' in modules_to_run:
            engine.add_module(GenerationModule(prompt_template=draw_prompt))
        if 'shorten' in modules_to_run:
            engine.add_module(ShortenModule(prompt_template=shorten_prompt))
        if 'validate' in modules_to_run and validation_prompt:
            engine.add_module(ValidatorModule(prompt_template=validation_prompt))
        
        # Execute
        results = engine.run()
        
        # Metrics
        if runtime_status["cancel_event"] and runtime_status["cancel_event"].is_set():
            runtime_status["last_result"] = "Pipeline was stopped by user."
        else:
            success_count = sum(1 for ctx in results for r in ctx.results if r.status == "success")
            runtime_status["last_result"] = f"Finished! Success items: {success_count}."
        
    except Exception as e:
        runtime_status["last_result"] = f"Fatal Error: {str(e)}"
    finally:
        runtime_status["is_running"] = False
        runtime_status["cancel_event"] = None


@app.route("/")
def index():
    prompts = PromptManager.get_all_prompts()
    # Default initialize if absolutely empty
    if not prompts.get("Analysis Prompts"):
        PromptManager.save_prompt("Analysis Prompts", "v4_default", "# 摄影师预设...")
        # Reload
        prompts = PromptManager.get_all_prompts()
        
    return render_template("index.html", 
                           config=current_config, 
                           prompts=prompts,
                           text_model_choices=TEXT_MODEL_CHOICES,
                           image_model_choices=IMAGE_MODEL_CHOICES,
                           default_analysis_key=DEFAULT_ANALYSIS_PROMPT_KEY,
                           default_shorten_key=DEFAULT_SHORTEN_PROMPT_KEY,
                           default_validation_key=DEFAULT_VALIDATION_PROMPT_KEY)


@app.route("/visualize")
def visualize_page():
    return render_template("visualize.html",
                           config=current_config,
                           text_model_choices=TEXT_MODEL_CHOICES,
                           image_model_choices=IMAGE_MODEL_CHOICES)


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.json
    try:
        current_config.input_directory = data.get("input_directory", current_config.input_directory)
        current_config.output_directory = data.get("output_directory", current_config.output_directory)
        current_config.max_workers = data.get("max_workers", current_config.max_workers)
        current_config.api_key = data.get("api_key") or current_config.api_key
        current_config.api_key_image = data.get("api_key_image") or current_config.api_key_image
        current_config.random_seed = int(data.get("random_seed", current_config.random_seed))
        current_config.sample_size = int(data.get("sample_size", current_config.sample_size))
        current_config.model_analysis = data.get("model_analysis", current_config.model_analysis)
        current_config.model_generation = data.get("model_generation", current_config.model_generation)
        current_config.model_shorten = data.get("model_shorten", current_config.model_shorten)
        
        return jsonify({"status": "success", "config": current_config.dict()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/api/run/<stage>", methods=["POST"])
def run_pipeline(stage):
    if runtime_status["is_running"]:
        return jsonify({"status": "error", "message": "Pipeline is already running!"}), 400
        
    data = request.json or {}
    
    # Load prompts - use provided values or fall back to prompts.md defaults
    prompts = PromptManager.get_all_prompts()
    analysis_prompt = data.get("prompt") or prompts.get("Analysis Prompts", {}).get("PROMPT_PLANNER_FOOD_v4", "")
    draw_prompt = data.get("draw_prompt") or prompts.get("Analysis Prompts", {}).get("PROMPT_DRAW_IMAGE", "")
    shorten_prompt = data.get("shorten_prompt") or prompts.get("Shorten Prompts", {}).get("PROMPT_SYS_SHORTEN_C", "")
    validation_prompt = data.get("validation_prompt") or prompts.get("Validation Prompts", {}).get(DEFAULT_VALIDATION_PROMPT_KEY, "")
    
    # Determine modules
    modules = []
    if stage == 'all':
        modules = ['analysis', 'generation', 'shorten', 'validate']
    elif stage in ['analysis', 'generation', 'shorten', 'validate']:
        modules = [stage]
    else:
        return jsonify({"status": "error", "message": "Invalid stage"}), 400
    
    # Start asynchronously so web UI doesn't block
    thread = Thread(target=run_pipeline_thread, args=(current_config, analysis_prompt, draw_prompt, shorten_prompt, validation_prompt, modules))
    thread.daemon = True
    thread.start()
    
    return jsonify({"status": "success", "message": f"Execution started for: {stage}"})

@app.route("/api/status", methods=["GET"])
def get_status():
    logs = ""
    if hasattr(sys.stdout, 'get_logs'):
        logs = sys.stdout.get_logs()
        
    return jsonify({
        "is_running": runtime_status["is_running"],
        "is_cancelling": runtime_status["cancel_event"] is not None and runtime_status["cancel_event"].is_set(),
        "last_result": runtime_status["last_result"],
        "logs": logs
    })

@app.route("/api/stop", methods=["POST"])
def stop_pipeline():
    if not runtime_status["is_running"]:
        return jsonify({"status": "error", "message": "No pipeline is currently running."}), 400
    
    if runtime_status["cancel_event"]:
        runtime_status["cancel_event"].set()
        return jsonify({"status": "success", "message": "Stop signal sent. Pipeline will terminate gracefully."})
    
    return jsonify({"status": "error", "message": "Cannot stop: no cancel event available."}), 500

@app.route("/api/stats", methods=["GET"])
def get_stats():
    import glob
    
    # Very basic stats
    input_files = []
    valid_exts = {'.jpg', '.jpeg', '.png', '.webp'}
    if os.path.exists(current_config.input_directory):
        for root, _, files in os.walk(current_config.input_directory):
            for f in files:
                if os.path.splitext(f)[1].lower() in valid_exts:
                    input_files.append(f)
                    
    generated_jsons = glob.glob(os.path.join(current_config.output_directory, "**/*.json"), recursive=True)
    generated_images = glob.glob(os.path.join(current_config.output_directory, "**/*.jpg"), recursive=True)
    
    return jsonify({
        "source_images_count": len(input_files),
        "generated_jsons_count": len(generated_jsons),
        "generated_images_count": len(generated_images),
        "output_dir": current_config.output_directory
    })

@app.route("/api/prompts", methods=["GET"])
def get_prompts():
    return jsonify(PromptManager.get_all_prompts())

@app.route("/api/prompts", methods=["POST"])
def save_prompt():
    data = request.json
    cat = data.get("category")
    name = data.get("name")
    content = data.get("content")
    
    if not cat or not name or not content:
        return jsonify({"status": "error", "message": "Missing fields"}), 400
        
    try:
        PromptManager.save_prompt(cat, name, content)
        return jsonify({"status": "success", "message": "Prompt saved successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== Visualization APIs ====================

@app.route("/api/visualize", methods=["GET"])
def api_visualize():
    """Scan input/output directories and return structured lightweight data for visualization."""
    input_dir = request.args.get("input_dir", current_config.input_directory)
    output_dir = request.args.get("output_dir", current_config.output_directory)
    seed_val = int(request.args.get("seed", current_config.random_seed))

    if not os.path.isdir(input_dir):
        return jsonify({"status": "error", "message": f"Input directory not found: {input_dir}"}), 400

    source_images = find_valid_images(input_dir)
    result_list = []

    for img_path in source_images:
        stem = os.path.splitext(os.path.basename(img_path))[0]
        seed = deterministic_seed(seed_val, stem)
        rel_path = os.path.relpath(img_path, input_dir)
        target_dir = os.path.join(output_dir, os.path.dirname(rel_path))

        # We do a fast check just to see if the target directory exists and has some files.
        # Even if not, we can include it so the user sees the original image pending generation.
        result_list.append({
            "original_path": img_path,
            "relative_path": rel_path,
            "seed": seed,
            "target_dir": target_dir,
            "stem": stem
        })

    return jsonify({
        "status": "success",
        "input_dir": input_dir,
        "output_dir": output_dir,
        "images": result_list,
    })

@app.route("/api/visualize/details", methods=["POST"])
def api_visualize_details():
    """Fetch details (analysis, schemes) for a given list of image items."""
    data = request.json or {}
    items = data.get("items", [])
    
    results = []
    
    for item in items:
        target_dir = item.get("target_dir")
        stem = item.get("stem")
        seed = item.get("seed")
        
        if not target_dir or not stem or not seed:
            continue
            
        analysis_txt_path = os.path.join(target_dir, f"{stem}_{seed}_analysis.txt")
        analysis_text = ""
        if os.path.isfile(analysis_txt_path):
            try:
                with open(analysis_txt_path, 'r', encoding='utf-8') as f:
                    analysis_text = f.read().strip()
            except Exception:
                pass

        schemes = []
        for n in range(1, 5):
            import glob
            pattern = os.path.join(target_dir, f"{stem}_p{n}_*_{seed}.jpg")
            pattern_simple = os.path.join(target_dir, f"{stem}_p{n}_{seed}.jpg")
            
            image_path = None
            if os.path.isfile(pattern_simple):
                image_path = pattern_simple
            else:
                matches = glob.glob(pattern)
                if matches:
                    image_path = matches[0]

            json_pattern = os.path.join(target_dir, f"{stem}_p{n}_*_{seed}.json")
            json_simple = os.path.join(target_dir, f"{stem}_p{n}_{seed}.json")
            
            json_path = None
            if os.path.isfile(json_simple):
                json_path = json_simple
            else:
                json_matches = glob.glob(json_pattern)
                if json_matches:
                    json_path = json_matches[0]

            scheme_data = {
                "n": n,
                "has_image": image_path is not None,
                "image_path": image_path,
                "theme": "",
                "short_plan": "",
                "status": "pending",
            }

            if json_path and os.path.isfile(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        jdata = json.load(f)
                    scheme_data.update(jdata)
                    scheme_data["theme"] = jdata.get("theme", "")
                    scheme_data["short_plan"] = jdata.get("short_plan", "")
                    scheme_data["status"] = jdata.get("status", "pending")
                except Exception:
                    pass
            elif image_path:
                scheme_data["status"] = "generated"

            if image_path or json_path or (analysis_text and f"方案{n}" in analysis_text):
                schemes.append(scheme_data)
                
        # Only populate the detailed schema for this item
        item_copy = dict(item)
        item_copy["analysis_text"] = analysis_text
        item_copy["has_analysis"] = bool(analysis_text)
        item_copy["schemes"] = schemes
        results.append(item_copy)

    return jsonify({
        "status": "success",
        "details": results
    })



@app.route("/api/image", methods=["GET"])
def serve_image():
    """Serve an image from an absolute path (for visualization)."""
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"status": "error", "message": "Image not found"}), 404
    
    # Basic validation: only serve image files
    ext = os.path.splitext(path)[1].lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}:
        return jsonify({"status": "error", "message": "Not an image file"}), 400
    
    return send_file(path, mimetype=f"image/{ext.lstrip('.')}")


@app.route("/visualize_errors")
def visualize_errors_page():
    return render_template("visualize_errors.html", config=current_config)

@app.route("/api/visualize/errors", methods=["GET"])
def api_visualize_errors():
    import glob
    import json
    import os
    from flask import request, jsonify
    output_dir = request.args.get("output_dir", current_config.output_directory)
    
    json_paths = glob.glob(os.path.join(output_dir, "**/*.json"), recursive=True)
    
    error_items = []
    
    for jp in json_paths:
        if "error_visual_results" in jp:
            continue
        try:
            with open(jp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            val = data.get("validation")
            if val and isinstance(val, dict) and val.get("has_error"):
                base_dir = os.path.dirname(jp)
                img_key = os.path.basename(data.get("generated_image_path", "")) if data.get("generated_image_path") else ""
                gen_path = os.path.join(base_dir, img_key) if img_key else ""
                
                orig_path = data.get("original_image_path", "")
                if orig_path and not os.path.isabs(orig_path):
                    test_abs = os.path.join(current_config.input_directory, orig_path)
                    if os.path.exists(test_abs):
                        orig_path = test_abs
                    elif os.path.exists(os.path.join(base_dir, orig_path)):
                        orig_path = os.path.join(base_dir, orig_path)
                        
                error_items.append({
                    "generated_path": gen_path,
                    "original_path": orig_path,
                    "error_ids": val.get("error_ids"),
                    "level": val.get("level"),
                    "reason": val.get("reason"),
                    "theme": data.get("theme", ""),
                    "image_key": img_key
                })
        except Exception as e:
            continue
            
    return jsonify({
        "status": "success",
        "errors": error_items
    })


if __name__ == "__main__":
    app.run(debug=True, port=8080)
