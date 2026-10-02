"""Fixtures compartilhadas dos testes web do kb_admin (Dify simulado)."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kb_admin.config import Settings
from kb_admin.errors import AuthError
from kb_admin.storage import ManualStore
from kb_admin.web import create_app

PASSWORD = "s3nh@"


class FakeAuthClient:
    """Dify simulado: ana é admin, edu é editor (só consulta) e normal não tem permissão."""

    def authenticate(self, email: str, password: str) -> str:
        """Simula o login. Entrada: e-mail e senha. Saída: papel; AuthError se inválido."""
        if email == "normal@camara.rj":
            raise AuthError("Sem permissão para gerenciar a base de conhecimento (seu papel no Dify: normal).")
        roles = {"ana@camara.rj": "admin", "edu@camara.rj": "editor"}
        if email not in roles or password != PASSWORD:
            raise AuthError("E-mail ou senha inválidos.")
        return roles[email]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Configuração de teste. Saída: Settings."""
    return Settings(
        assets_dir=tmp_path / "assets",
        data_dir=tmp_path / "data",
        assets_base_url="http://dify.test/kb-assets",
        dify_api_url="http://api:5001",
        secret_key="segredo-de-teste",
        child_max_length=1000,
        max_segmentation_length=4000,
        embedding_model_label="text-embedding-3-small",
        rerank_model_label="jina-reranker-v3",
    )


@pytest.fixture
def store(settings: Settings) -> ManualStore:
    """Store em pastas temporárias. Saída: ManualStore."""
    manual_store = ManualStore(settings.assets_dir, settings.data_dir)
    manual_store.ensure_dirs()
    return manual_store


@pytest.fixture
def client(settings: Settings, store: ManualStore) -> TestClient:
    """Cliente HTTP sem login. Saída: TestClient."""
    return TestClient(create_app(settings, store, FakeAuthClient()))


@pytest.fixture
def logged_client(client: TestClient) -> TestClient:
    """Cliente já logado como editor. Saída: TestClient."""
    response = client.post(
        "/kb-admin/login", data={"email": "ana@camara.rj", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def csrf(client: TestClient) -> str:
    """Lê o token CSRF da página inicial. Entrada: cliente logado. Saída: token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/kb-admin/").text)
    assert match is not None
    return match.group(1)


@pytest.fixture
def editor_client(settings: Settings, store: ManualStore) -> TestClient:
    """Cliente logado como editor (somente leitura). Saída: TestClient."""
    client = TestClient(create_app(settings, store, FakeAuthClient()))
    response = client.post(
        "/kb-admin/login", data={"email": "edu@camara.rj", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client
