"""Testes do perfil somente leitura (editor) nas rotas de manuais."""
from fastapi.testclient import TestClient

from kb_admin.config import Settings
from tests.conftest import csrf
from tests.helpers import manual_files, zip_bytes


def publish_as_admin(client: TestClient) -> None:
    """Publica o manual de teste como admin. Entrada: cliente admin. Saída: nenhuma."""
    response = client.post(
        "/kb-admin/upload",
        data={"csrf_token": csrf(client)},
        files={"file": ("manual-teste.zip", zip_bytes(manual_files()), "application/zip")},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_editor_sees_but_has_no_change_buttons(logged_client: TestClient, editor_client: TestClient) -> None:
    """O editor vê lista e manual, baixa o .md, mas não vê botões de alteração."""
    publish_as_admin(logged_client)
    listing = editor_client.get("/kb-admin/").text
    assert "manual-teste" in listing and "Enviar manual" not in listing
    page = editor_client.get("/kb-admin/manuals/manual-teste").text
    assert "Baixar .md para o Dify" in page
    assert "Substituir" not in page and ">Apagar<" not in page
    assert editor_client.get("/kb-admin/manuals/manual-teste/download").status_code == 200


def test_editor_cannot_change_manuals(
    logged_client: TestClient, editor_client: TestClient, settings: Settings
) -> None:
    """Toda rota de alteração de manual responde 403 ao editor, mesmo com CSRF válido."""
    publish_as_admin(logged_client)
    token = csrf(editor_client)
    assert editor_client.get("/kb-admin/upload").status_code == 403
    upload = editor_client.post(
        "/kb-admin/upload",
        data={"csrf_token": token},
        files={"file": ("manual-teste.zip", zip_bytes(manual_files()), "application/zip")},
    )
    assert upload.status_code == 403
    assert "Seu papel no Dify (editor) permite só consulta." in upload.text
    assert editor_client.get("/kb-admin/manuals/manual-teste/delete").status_code == 403
    deleted = editor_client.post("/kb-admin/manuals/manual-teste/delete", data={"csrf_token": token})
    assert deleted.status_code == 403
    fake = "x" * 32
    assert editor_client.post(f"/kb-admin/upload/{fake}/confirm", data={"csrf_token": token}).status_code == 403
    assert editor_client.post(f"/kb-admin/upload/{fake}/cancel", data={"csrf_token": token}).status_code == 403
    assert (settings.assets_dir / "manual-teste/tela.png").exists()


def test_admin_sees_change_buttons(logged_client: TestClient) -> None:
    """O admin continua vendo os botões."""
    publish_as_admin(logged_client)
    assert "Enviar manual" in logged_client.get("/kb-admin/").text
    assert "Substituir" in logged_client.get("/kb-admin/manuals/manual-teste").text
