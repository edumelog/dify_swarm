"""Teste do ponto de entrada (build_app) com o ambiente simulado."""
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
