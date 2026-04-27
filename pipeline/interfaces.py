from abc import ABC, abstractmethod
from typing import Any, Dict
from .models import PipelineConfig, PipelineResult

class PipelineContext:
    """
    A runtime object passed between modules during execution.
    Holds the current state, config, and intermediate results.
    """
    def __init__(self, config: PipelineConfig, original_image_path: str):
        self.config = config
        self.original_image_path = original_image_path
        
        # Common extracted data
        self.original_image_base64: str = ""
        self.analysis_text: str = ""
        self.parsed_schemes: list[Dict[str, str]] = []  # List of dicts with 'theme' and 'content'
        
        # The list of results tracking generating each scheme.
        self.results: list[PipelineResult] = []

class PipelineModule(ABC):
    """
    Abstract Base Class for all pipeline processing stages.
    """
    
    @abstractmethod
    def process(self, context: PipelineContext) -> PipelineContext:
        """
        Process the given context. Modifies context in-place or returns updated context.
        Must raise Exceptions on critical failures so the engine can catch them.
        """
        pass
