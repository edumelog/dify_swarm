"""Testes das rotas web do kb_admin: login, sessão, CSRF e lista."""
import pytest
from fastapi.testclient import TestClient

from kb_admin.config import Settings
from tests.conftest import PASSWORD, csrf


def test_healthz_without_login(client: TestClient) -> None:
    """Healthcheck responde sem login."""
    assert client.get("/kb-admin/healthz").text == "ok"


def test_pages_require_login(client: TestClient) -> None:
    """Sem sessão, a lista redireciona para o login."""
    response = client.get("/kb-admin/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/kb-admin/login"


def test_login_sets_scoped_cookie_and_shows_list(client: TestClient) -> None:
    """Login certo cria o cookie restrito a /kb-admin e mostra a lista vazia e o ambiente."""
    response = client.post(
        "/kb-admin/login", data={"email": "ana@camara.rj", "password": PASSWORD}, follow_redirects=False
    )
    cookie = response.headers["set-cookie"]
    assert "kb_admin_session=" in cookie
    assert "Path=/kb-admin" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Secure" not in cookie
    page = client.get("/kb-admin/").text
    assert "Nenhum manual publicado ainda." in page
    assert "http://dify.test/kb-assets/" in page
    assert "ana@camara.rj" in page


def test_login_over_https_proxy_sets_secure_cookie(client: TestClient) -> None:
    """Atrás do NGPM com https, o cookie sai com Secure."""
    response = client.post(
        "/kb-admin/login",
        data={"email": "ana@camara.rj", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("email", "message"),
    [("ana@camara.rj", "E-mail ou senha inválidos."), ("normal@camara.rj", "Sem permissão")],
)
def test_login_failures(client: TestClient, email: str, message: str) -> None:
    """Falha de login mostra a mensagem e não cria sessão."""
    response = client.post("/kb-admin/login", data={"email": email, "password": "errada"})
    assert response.status_code == 401
    assert message in response.text
    assert "kb_admin_session" not in response.headers.get("set-cookie", "")


def test_logout_requires_csrf(logged_client: TestClient) -> None:
    """Sair sem CSRF é recusado; com CSRF apaga a sessão."""
    assert logged_client.post("/kb-admin/logout", data={"csrf_token": "errado"}).status_code == 403
    response = logged_client.post(
        "/kb-admin/logout", data={"csrf_token": csrf(logged_client)}, follow_redirects=False
    )
    assert response.headers["location"] == "/kb-admin/login"
    assert logged_client.get("/kb-admin/", follow_redirects=False).status_code == 303


def test_tampered_session_is_rejected(client: TestClient) -> None:
    """Cookie adulterado equivale a não estar logado."""
    response = client.get("/kb-admin/", headers={"Cookie": "kb_admin_session=falso"}, follow_redirects=False)
    assert response.status_code == 303


def test_list_shows_legacy_folder(logged_client: TestClient, settings: Settings) -> None:
    """Pasta publicada pelo publish.sh aparece marcada como fora da interface."""
    (settings.assets_dir / "office365").mkdir()
    page = logged_client.get("/kb-admin/").text
    assert "office365" in page and "publicado fora da interface" in page



def _assert_theme_toggle(page: str) -> None:
    """Confere botão, tema escuro e preferência salva numa página. Entrada: HTML. Saída: nenhuma."""
    assert 'id="theme-toggle"' in page
    assert ':root[data-theme="dark"]' in page
    assert "prefers-color-scheme: dark" in page
    assert "kb_admin_theme" in page


def test_dark_mode_toggle_on_login(client: TestClient) -> None:
    """O botão de modo escuro aparece na tela de login."""
    _assert_theme_toggle(client.get("/kb-admin/login").text)


def test_dark_mode_toggle_when_logged(logged_client: TestClient) -> None:
    """O botão de modo escuro aparece nas páginas logadas."""
    _assert_theme_toggle(logged_client.get("/kb-admin/").text)
    _assert_theme_toggle(logged_client.get("/kb-admin/docs").text)
