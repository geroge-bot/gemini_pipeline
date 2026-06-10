from pathlib import Path

from scripts.configure_annotations_v2_nginx import build_nginx_config, preview_cache_dir_from_env


def test_build_nginx_config_uses_preview_cache_dir_for_annotation_assets():
    config = build_nginx_config(
        preview_cache_dir=Path("/srv/annotations_v2/preview-cache"),
        flask_upstream="http://127.0.0.1:5065",
        server_name="annotations.example.com",
    )

    assert "server_name annotations.example.com;" in config
    assert "proxy_pass http://127.0.0.1:5065;" in config
    assert "location /annotation-assets/ {" in config
    assert 'alias "/srv/annotations_v2/preview-cache/";' in config
    assert 'add_header Cache-Control "public, max-age=31536000, immutable";' in config
    assert "try_files $uri =404;" in config


def test_preview_cache_dir_from_env_requires_annotations_v2_preview_cache_dir(monkeypatch):
    monkeypatch.delenv("ANNOTATIONS_V2_PREVIEW_CACHE_DIR", raising=False)

    try:
        preview_cache_dir_from_env()
    except RuntimeError as exc:
        assert "ANNOTATIONS_V2_PREVIEW_CACHE_DIR" in str(exc)
    else:
        raise AssertionError("preview_cache_dir_from_env should require the environment variable")


def test_preview_cache_dir_from_env_normalizes_trailing_slash(monkeypatch):
    monkeypatch.setenv("ANNOTATIONS_V2_PREVIEW_CACHE_DIR", "/srv/annotations_v2/preview-cache/")

    assert preview_cache_dir_from_env() == Path("/srv/annotations_v2/preview-cache")
