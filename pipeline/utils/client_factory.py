"""
Factory for creating API clients from service configurations.
Reads from services.md via ServiceManager and returns the appropriate client.
"""

from typing import Optional
from pipeline.utils.service_manager import ServiceManager, ServiceConfig
from pipeline.utils.api_client import GeminiAPIClient
from pipeline.utils.gemini_native_client import GeminiNativeAPIClient


def create_client_from_service(service_name: str):
    """
    Create and return an API client based on the service config in services.md.

    Args:
        service_name: The service name as defined in services.md (e.g. 'yunwu_text', 'gemini_text')

    Returns:
        GeminiAPIClient (for type=openai) or GeminiNativeAPIClient (for type=gemini_native)

    Raises:
        ValueError: If the service is not found or has an unknown type.
    """
    svc = ServiceManager.get_service(service_name)
    if svc is None:
        available = ServiceManager.list_service_names()
        raise ValueError(
            f"Service '{service_name}' not found in services.md. "
            f"Available services: {available}"
        )

    return _create_client_from_config(svc)


def create_text_image_clients(text_service: str, image_service: str):
    """
    Convenience function to create both text and image clients at once.

    Args:
        text_service:  Service name for text generation.
        image_service: Service name for image generation.

    Returns:
        Tuple of (text_client, image_client)
    """
    return create_client_from_service(text_service), create_client_from_service(image_service)


def _create_client_from_config(svc: ServiceConfig):
    """Create the appropriate client based on service type."""
    if svc.type == "openai":
        return GeminiAPIClient(
            api_key=svc.api_key,
            base_url=svc.base_url,
            service_name=svc.name,
        )
    elif svc.type == "gemini_native":
        return GeminiNativeAPIClient(
            api_key=svc.api_key,
            base_url=svc.base_url,
            service_name=svc.name,
        )
    else:
        raise ValueError(
            f"Unknown service type '{svc.type}' for service '{svc.name}'. "
            f"Supported types: openai, gemini_native"
        )
