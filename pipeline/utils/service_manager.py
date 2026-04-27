"""
Manages loading and saving service configurations from a Markdown file.
Each service entry has: type, api_key, base_url, model, description.
"""

import os
import re
from typing import Dict, Optional

SERVICES_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services.md")


class ServiceConfig:
    """Represents a single service configuration entry."""

    def __init__(self, name: str, service_type: str, api_key: str,
                 base_url: str, model: str = "", description: str = ""):
        self.name = name
        self.type = service_type
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.description = description

    def __repr__(self):
        return (f"ServiceConfig(name={self.name!r}, type={self.type!r}, "
                f"base_url={self.base_url!r}, model={self.model!r})")


class ServiceManager:
    """
    Parses services.md to load/save service configurations.

    Markdown format:
        ## service_name
        - **type**: openai | gemini_native
        - **api_key**: sk-xxx
        - **base_url**: https://...
        - **model**: model-name
        - **description**: 描述文字
    """

    @staticmethod
    def _ensure_file_exists():
        if not os.path.exists(SERVICES_FILE_PATH):
            with open(SERVICES_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write("# Service Configurations\n\n"
                        "通过此文件管理不同 Gemini 服务的 API Key 和服务地址。\n\n")

    @staticmethod
    def get_all_services() -> Dict[str, ServiceConfig]:
        """
        Parses the services.md file and returns a dict of service name -> ServiceConfig.
        """
        ServiceManager._ensure_file_exists()

        services: Dict[str, ServiceConfig] = {}

        with open(SERVICES_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split by ## headings (service names)
        current_name: Optional[str] = None
        current_props: Dict[str, str] = {}

        for line in content.split('\n'):
            # Detect service heading: ## service_name
            if line.startswith('## '):
                # Save previous service if exists
                if current_name and current_props.get('type') and current_props.get('api_key'):
                    services[current_name] = ServiceConfig(
                        name=current_name,
                        service_type=current_props.get('type', ''),
                        api_key=current_props.get('api_key', ''),
                        base_url=current_props.get('base_url', ''),
                        model=current_props.get('model', ''),
                        description=current_props.get('description', ''),
                    )
                current_name = line[3:].strip()
                current_props = {}
            elif current_name:
                # Parse property lines: - **key**: value
                match = re.match(r'^-\s+\*\*(\w+)\*\*:\s*(.+)$', line.strip())
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    # Strip angle brackets from markdown auto-link syntax: <url> -> url
                    if value.startswith('<') and value.endswith('>'):
                        value = value[1:-1]
                    current_props[key] = value

        # Save last service
        if current_name and current_props.get('type') and current_props.get('api_key'):
            services[current_name] = ServiceConfig(
                name=current_name,
                service_type=current_props.get('type', ''),
                api_key=current_props.get('api_key', ''),
                base_url=current_props.get('base_url', ''),
                model=current_props.get('model', ''),
                description=current_props.get('description', ''),
            )

        return services

    @staticmethod
    def get_service(name: str) -> Optional[ServiceConfig]:
        """Get a single service config by name. Returns None if not found."""
        services = ServiceManager.get_all_services()
        return services.get(name)

    @staticmethod
    def save_service(name: str, service_type: str, api_key: str,
                     base_url: str, model: str = "", description: str = ""):
        """Saves or updates a service entry in the markdown file."""
        services = ServiceManager.get_all_services()

        services[name] = ServiceConfig(
            name=name,
            service_type=service_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            description=description,
        )

        # Rewrite the entire file
        ServiceManager._write_all(services)

    @staticmethod
    def delete_service(name: str) -> bool:
        """Deletes a service entry. Returns True if the entry existed."""
        services = ServiceManager.get_all_services()
        if name not in services:
            return False
        del services[name]
        ServiceManager._write_all(services)
        return True

    @staticmethod
    def _write_all(services: Dict[str, ServiceConfig]):
        """Rewrite the entire services.md file from the services dict."""
        with open(SERVICES_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write("# Service Configurations\n\n")
            f.write("通过此文件管理不同 Gemini 服务的 API Key 和服务地址。\n")
            f.write("每个 `##` 标题代表一个服务名称，下方列出该服务的配置项。\n\n")

            for svc_name, svc in services.items():
                f.write(f"## {svc_name}\n\n")
                f.write(f"- **type**: {svc.type}\n")
                f.write(f"- **api_key**: {svc.api_key}\n")
                f.write(f"- **base_url**: {svc.base_url}\n")
                f.write(f"- **model**: {svc.model}\n")
                f.write(f"- **description**: {svc.description}\n\n")

    @staticmethod
    def list_service_names() -> list:
        """Returns a list of all registered service names."""
        return list(ServiceManager.get_all_services().keys())
