import os
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from pipeline.config import (
    DEFAULT_API_KEY, DEFAULT_API_KEY_IMAGE, DEFAULT_API_BASE_URL,
    DEFAULT_SHORTEN_MODE, DEFAULT_MAX_WORKERS, DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLE_SIZE,
    DEFAULT_MODEL_ANALYSIS, DEFAULT_MODEL_GENERATION, DEFAULT_MODEL_SHORTEN,
)

class PipelineResult(BaseModel):
    """
    Unified result object that will be serialized to JSON and saved alongside the image.
    """
    theme: str = Field(..., description="The theme of the generated image scheme.")
    original_image_path: str = Field(..., description="Absolute or relative path to the original image.")
    generated_image_path: Optional[str] = Field(None, description="Path to the newly generated image, if successful.")
    original_plan: str = Field(..., description="The raw analysis plan output by the vision model.")
    short_plan: Optional[str] = Field(None, description="The refined, <120 words actionable summary.")
    analysis_prompt_used: str = Field(..., description="The raw prompt string used for the vision analysis stage.")
    shorten_prompt_used: Optional[str] = Field(None, description="The raw prompt string used for the summary stage.")
    mode: str = Field(..., description="The shortening mode used (e.g., 'C').")
    camera_movement: Optional[Dict[str, str]] = Field(None, description="Extracted camera movement parameters (pitch, yaw, etc.).")
    validation: Optional[Dict[str, Any]] = Field(None, description="Validation results including error fields.")
    description: Optional[Dict[str, Any]] = Field(None, description="Description module output: question, persona, response, and conversation history.")
    labels: Optional[Dict[str, Any]] = Field(None, description="Two-image labeling output parsed from the vision model.")
    status: str = Field("pending", description="Status of the pipeline: 'pending', 'success', 'failed'.")
    error_message: Optional[str] = Field(None, description="Detailed error trace if the pipeline failed on this item.")

    def save_json(self, output_dir: str, filename_override: Optional[str] = None):
        """Helper to save the current state as a formatted JSON file."""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            
        fname = filename_override if filename_override else "result.json"
        
        # If generated_image_path exists, prefer to name it similarly
        if self.generated_image_path and not filename_override:
            base = os.path.basename(self.generated_image_path)
            fname = os.path.splitext(base)[0] + ".json"
            
        save_path = os.path.join(output_dir, fname)
        
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(self.model_dump_json(indent=4))
        
        return save_path

class PipelineConfig(BaseModel):
    """
    Configuration payload for the pipeline engine, adjustable via Web UI.
    """
    input_directory: str = Field(..., description="Directory containing source images.")
    output_directory: str = Field(..., description="Directory to save generated images and JSONs.")
    prompt_template_key: str = Field("v4", description="Key for the prompt to use from prompt dictionary.")
    shorten_mode: str = Field(DEFAULT_SHORTEN_MODE, description="Mode for shortening the generated text (A, B, C).")
    max_workers: int = Field(DEFAULT_MAX_WORKERS, description="Number of concurrent execution threads.")
    api_key: Optional[str] = Field(DEFAULT_API_KEY, description="Yunwu API key for text generation.")
    api_key_image: Optional[str] = Field(DEFAULT_API_KEY_IMAGE, description="Yunwu API key for image processing.")
    api_base_url: str = Field(DEFAULT_API_BASE_URL, description="API Base URL.")
    random_seed: int = Field(DEFAULT_RANDOM_SEED, description="Random seed for deterministic file naming.")
    model_analysis: str = Field(DEFAULT_MODEL_ANALYSIS, description="Model for analysis stage (text).")
    model_generation: str = Field(DEFAULT_MODEL_GENERATION, description="Model for image generation stage.")
    model_shorten: str = Field(DEFAULT_MODEL_SHORTEN, description="Model for shorten stage (text).")
    sample_size: int = Field(DEFAULT_SAMPLE_SIZE, description="Number of source images to process (0 for all).")
