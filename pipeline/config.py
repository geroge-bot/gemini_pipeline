"""
Centralized configuration defaults for the Food Photography Pipeline.
All hardcoded constants (API keys, directories, prompt versions, etc.) live here.
"""

# ====================== API Keys ======================
DEFAULT_API_KEY = "sk-AMFmnFC1IubXcVj9p39tLt2JxmgvQcJJ8BOyTI9ewtwhejtD"
DEFAULT_API_KEY_IMAGE = "sk-XpFCNWUliI2CA3bXAWAirstGXm5HfH5Is98bGBZq6xyKF4xD"
DEFAULT_API_BASE_URL = "https://yunwu.ai/v1"

# ====================== Model Defaults (per stage) ======================
DEFAULT_MODEL_ANALYSIS = "gemini-3.1-pro-preview"
DEFAULT_MODEL_GENERATION = "gemini-3-pro-image-preview"
DEFAULT_MODEL_SHORTEN = "gemini-3.1-pro-preview"

TEXT_MODEL_CHOICES = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
]

IMAGE_MODEL_CHOICES = [
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
]

# ====================== Default Prompt Versions ======================
# Keys correspond to prompt names in utils/prompts.md
DEFAULT_ANALYSIS_PROMPT_KEY = "PROMPT_PLANNER_FOOD_v4"
DEFAULT_DRAW_PROMPT_KEY = "PROMPT_DRAW_IMAGE"
DEFAULT_SHORTEN_PROMPT_KEY = "PROMPT_SYS_SHORTEN_C"
DEFAULT_VALIDATION_PROMPT_KEY = "PROMPT_VALIDATE"
DEFAULT_SHORTEN_MODE = "C"

# ====================== Default Directories ======================
DEFAULT_INPUT_DIRECTORY = r"D:\test\food_test"
DEFAULT_OUTPUT_DIRECTORY = r"D:\test\food_test_output"

# ====================== Default Service Names (services.md) ======================
# These names correspond to entries in pipeline/utils/services.md
DEFAULT_SERVICE_TEXT = "az_middle_text"
DEFAULT_SERVICE_IMAGE = "yunwu_image"
DEFAULT_SERVICE_GEMINI_TEXT = "az_middle_text"
DEFAULT_SERVICE_GEMINI_IMAGE = "yunwu_image"

# ====================== Pipeline Defaults ======================
DEFAULT_MAX_WORKERS = 5
DEFAULT_RANDOM_SEED = 42
DEFAULT_SAMPLE_SIZE = 0
