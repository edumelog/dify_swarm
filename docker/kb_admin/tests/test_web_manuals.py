"""Testes das rotas de envio, manual, download e exclusão."""
import re

import httpx
from fastapi.testclient import TestClient

from kb_admin.config import Settings
from tests.conftest import csrf
from tests.helpers import PNG, manual_files, zip_bytes


def upload(client: TestClient, files: dict, filename: str = "manual-teste.zip") -> httpx.Response:
    """Envia um zip. Entrada: cliente logado, conteúdo e nome. Saída: resposta (sem seguir redirecionamento)."""
    return client.post(
        "/kb-admin/upload",
        data={"csrf_token": csrf(client)},
        files={"file": (filename, zip_bytes(files), "application/zip")},
        follow_redirects=False,
    )


def pending_token(html: str) -> str:
    """Lê o token da tela de confirmação. Entrada: HTML. Saída: token."""
    match = re.search(r"/kb-admin/upload/([A-Za-z0-9_-]+)/confirm", html)
    assert match is not None
    return match.group(1)


def test_upload_new_manual_publishes(logged_client: TestClient, settings: Settings) -> None:
    """Manual novo e válido é publicado direto e abre a tela do manual."""
    response = upload(logged_client, manual_files())
    assert response.status_code == 303
    assert response.headers["location"] == "/kb-admin/manuals/manual-teste?published=1"
    assert (settings.assets_dir / "manual-teste/tela.png").exists()
    page = logged_client.get(response.headers["location"]).text
    assert "Manual publicado." in page
    assert "Parent-child" in page and "jina-reranker-v3" in page
    assert "http://dify.test/kb-assets/manual-teste/tela.png" in page
    assert "Este .md só funciona no Dify de dify.test" in page


def test_upload_requires_csrf(logged_client: TestClient) -> None:
    """Envio sem CSRF é recusado."""
    response = logged_client.post(
        "/kb-admin/upload",
        data={"csrf_token": "errado"},
        files={"file": ("manual-teste.zip", zip_bytes(manual_files()), "application/zip")},
    )
    assert response.status_code == 403


def test_upload_with_problems_publishes_nothing(logged_client: TestClient, settings: Settings) -> None:
    """Pacote com problemas lista tudo e não publica."""
    md = "## A\n\n![x](images/falta.png)\n"
    response = upload(logged_client, manual_files(markdown=md))
    assert response.status_code == 400
    assert "imagem referenciada não encontrada em images/: falta.png" in response.text
    assert list(settings.assets_dir.iterdir()) == []
    assert list((settings.data_dir / "pending").iterdir()) == []


def test_download_has_absolute_urls(logged_client: TestClient) -> None:
    """O .md baixado tem o nome do manual e as URLs absolutas do ambiente."""
    upload(logged_client, manual_files())
    response = logged_client.get("/kb-admin/manuals/manual-teste/download")
    assert response.headers["content-disposition"] == 'attachment; filename="manual-teste.md"'
    assert "](http://dify.test/kb-assets/manual-teste/tela.png)" in response.text
    assert "](images/" not in response.text


def test_replace_shows_diff_and_publishes_on_confirm(logged_client: TestClient, settings: Settings) -> None:
    """Reenviar mostra o que muda e só publica ao confirmar."""
    first_md = "## A\n\n![t](images/tela.png)\n![v](images/velha.png)\n"
    upload(logged_client, manual_files(images=("tela.png", "velha.png"), markdown=first_md))
    second_md = "## A\n\n![t](images/tela.png)\n![n](images/nova.png)\n"
    files = manual_files(images=("tela.png", "nova.png"), markdown=second_md)
    files["manual-teste/images/tela.png"] = PNG + b"alterada"
    response = upload(logged_client, files)
    assert response.status_code == 200
    assert "Entram" in response.text and "nova.png" in response.text
    assert "Saem" in response.text and "velha.png" in response.text
    assert "Mudam" in response.text
    assert (settings.assets_dir / "manual-teste/velha.png").exists()
    token = pending_token(response.text)
    confirmed = logged_client.post(
        f"/kb-admin/upload/{token}/confirm", data={"csrf_token": csrf(logged_client)}, follow_redirects=False
    )
    assert confirmed.status_code == 303
    assert not (settings.assets_dir / "manual-teste/velha.png").exists()
    assert (settings.assets_dir / "manual-teste/nova.png").exists()
    assert list((settings.data_dir / "pending").iterdir()) == []


def test_replace_cancel_keeps_current(logged_client: TestClient, settings: Settings) -> None:
    """Cancelar a substituição mantém o publicado e descarta o envio."""
    upload(logged_client, manual_files())
    token = pending_token(upload(logged_client, manual_files()).text)
    response = logged_client.post(
        f"/kb-admin/upload/{token}/cancel", data={"csrf_token": csrf(logged_client)}, follow_redirects=False
    )
    assert response.headers["location"] == "/kb-admin/"
    assert list((settings.data_dir / "pending").iterdir()) == []
    assert (settings.assets_dir / "manual-teste/tela.png").exists()


def test_replace_compares_parameters(logged_client: TestClient) -> None:
    """A confirmação destaca parâmetro que mudou."""
    upload(logged_client, manual_files())
    long_md = "## A\n\n" + "a" * 900 + "\n\n![t](images/tela.png)\n"
    page = upload(logged_client, manual_files(markdown=long_md)).text
    assert 'class="changed"' in page
    assert "reprocessado no Dify" in page


def test_delete_flow(logged_client: TestClient, settings: Settings) -> None:
    """Apagar pede confirmação, remove tudo e lembra do Dify."""
    upload(logged_client, manual_files())
    assert "Apagar manual-teste" in logged_client.get("/kb-admin/manuals/manual-teste/delete").text
    response = logged_client.post(
        "/kb-admin/manuals/manual-teste/delete", data={"csrf_token": csrf(logged_client)}
    )
    assert "Lembre-se de apagar o documento correspondente" in response.text
    assert not (settings.assets_dir / "manual-teste").exists()


def test_legacy_manual_page_and_delete(logged_client: TestClient, settings: Settings) -> None:
    """Pasta legada mostra a galeria, não oferece download e pode ser apagada."""
    (settings.assets_dir / "office365").mkdir()
    (settings.assets_dir / "office365/x.png").write_bytes(PNG)
    page = logged_client.get("/kb-admin/manuals/office365").text
    assert "publicada fora da interface" in page and "Baixar .md" not in page
    assert logged_client.get("/kb-admin/manuals/office365/download").status_code == 404
    logged_client.post("/kb-admin/manuals/office365/delete", data={"csrf_token": csrf(logged_client)})
    assert not (settings.assets_dir / "office365").exists()


def test_unknown_manual_is_404(logged_client: TestClient) -> None:
    """Manual inexistente responde 404 com mensagem."""
    response = logged_client.get("/kb-admin/manuals/nao-existe")
    assert response.status_code == 404
    assert "não encontrado" in response.text


def test_confirm_with_bad_token(logged_client: TestClient) -> None:
    """Token desconhecido dá mensagem de envio não encontrado."""
    response = logged_client.post(
        "/kb-admin/upload/" + "x" * 32 + "/confirm", data={"csrf_token": csrf(logged_client)}
    )
    assert response.status_code == 400
    assert "Envio não encontrado" in response.text


def test_new_manual_shows_package_warnings(logged_client: TestClient) -> None:
    """Avisos do pacote (ex.: imagem não citada) aparecem também quando o manual é novo."""
    response = upload(logged_client, manual_files(images=("tela.png", "sobra.png")))
    page = logged_client.get(response.headers["location"]).text
    assert "imagem não citada no .md (não será publicada): sobra.png" in page
