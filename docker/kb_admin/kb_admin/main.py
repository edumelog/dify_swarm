"""Ponto de entrada do kb_admin (uvicorn kb_admin.main:build_app --factory)."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from kb_admin.auth import DifyAuthClient
from kb_admin.config import load_settings
from kb_admin.storage import ManualStore
from kb_admin.web import create_app


def build_app() -> FastAPI:
    """Monta o app com o ambiente do container. Entrada: nenhuma (lê os.environ). Saída: FastAPI; ConfigError impede a subida."""
    logging.basicConfig(level=logging.INFO)
    settings = load_settings(os.environ)
    store = ManualStore(settings.assets_dir, settings.data_dir)
    store.ensure_dirs()
    store.cleanup_temporary()
    return create_app(settings, store, DifyAuthClient(settings.dify_api_url))
