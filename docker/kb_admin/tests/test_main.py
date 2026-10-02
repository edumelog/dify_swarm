"""Teste do ponto de entrada (build_app) com o ambiente simulado."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kb_admin.main import build_app


def test_build_app_reads_env_and_cleans_leftovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """build_app lê o ambiente, cria as pastas e limpa temporários antigos."""
    assets = tmp_path / "assets"
    (assets / ".manual.tmp-abc").mkdir(parents=True)
    monkeypatch.setenv("KB_ASSETS_DIR", str(assets))
    monkeypatch.setenv("KB_ADMIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_WEB_URL", "http://dify.dev.dti")
    monkeypatch.setenv("SECRET_KEY", "x")
    client = TestClient(build_app())
    assert client.get("/kb-admin/healthz").text == "ok"
    assert (tmp_path / "data/pending").is_dir()
    assert not (assets / ".manual.tmp-abc").exists()


def test_build_app_seeds_conversion_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Na primeira subida, o prompt de conversão entra na biblioteca de documentos."""
    monkeypatch.setenv("KB_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("KB_ADMIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_WEB_URL", "http://dify.dev.dti")
    monkeypatch.setenv("SECRET_KEY", "x")
    build_app()
    assert (tmp_path / "data/docs/.seeded").exists()
    metas = [json.loads(p.read_text()) for p in (tmp_path / "data/docs").glob("*/meta.json")]
    assert [meta["filename"] for meta in metas] == ["PDF_TO_RAG.md"]
    assert metas[0]["updated_by"] == "conteúdo inicial"
