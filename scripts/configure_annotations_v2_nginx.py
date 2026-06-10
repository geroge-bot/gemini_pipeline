from __future__ import annotations

import argparse
import os
from pathlib import Path


PREVIEW_CACHE_DIR_ENV = "ANNOTATIONS_V2_PREVIEW_CACHE_DIR"
DEFAULT_FLASK_UPSTREAM = "http://127.0.0.1:5065"


def preview_cache_dir_from_env() -> Path:
    value = os.environ.get(PREVIEW_CACHE_DIR_ENV, "").strip()
    if not value:
        raise RuntimeError(f"{PREVIEW_CACHE_DIR_ENV} is required")
    return Path(value).expanduser()


def nginx_safe_value(value: str, field_name: str) -> str:
    if any(char in value for char in "\r\n;{}"):
        raise ValueError(f"{field_name} contains characters that are unsafe for nginx config")
    return value


def nginx_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def directory_alias_path(path: Path) -> str:
    text = str(path.expanduser())
    return text.rstrip("/") + "/"


def build_nginx_config(
    preview_cache_dir: Path,
    flask_upstream: str = DEFAULT_FLASK_UPSTREAM,
    server_name: str = "_",
    listen: str = "80",
) -> str:
    alias_path = directory_alias_path(preview_cache_dir)
    safe_upstream = nginx_safe_value(flask_upstream, "flask_upstream")
    safe_server_name = nginx_safe_value(server_name, "server_name")
    safe_listen = nginx_safe_value(listen, "listen")
    return f"""server {{
    listen {safe_listen};
    server_name {safe_server_name};

    client_max_body_size 100m;

    location /annotation-assets/ {{
        alias {nginx_quote(alias_path)};
        access_log off;
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }}

    location / {{
        proxy_pass {safe_upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an nginx server config for annotations_v2 static preview assets."
    )
    parser.add_argument(
        "--preview-cache-dir",
        type=Path,
        default=None,
        help=f"Preview cache directory. Defaults to ${PREVIEW_CACHE_DIR_ENV}.",
    )
    parser.add_argument("--flask-upstream", default=DEFAULT_FLASK_UPSTREAM)
    parser.add_argument("--server-name", default="_")
    parser.add_argument("--listen", default="80")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write config to this path. Defaults to stdout.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    preview_cache_dir = args.preview_cache_dir or preview_cache_dir_from_env()
    config = build_nginx_config(
        preview_cache_dir=preview_cache_dir,
        flask_upstream=args.flask_upstream,
        server_name=args.server_name,
        listen=args.listen,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(config, encoding="utf-8")
        print(f"Wrote nginx config to {args.output}")
    else:
        print(config, end="")


if __name__ == "__main__":
    main()
