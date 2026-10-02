"""Testes da aba Documentos de apoio."""
import re

import httpx
from fastapi.testclient import TestClient

from tests.conftest import csrf


def create_doc(client: TestClient, name: str = "prompt.md", content: bytes = b"# Prompt", desc: str = "Prompt") -> httpx.Response:
    """Envia um documento pela tela. Entrada: cliente, nome, conteúdo e descrição. Saída: resposta sem seguir redirecionamento."""
    return client.post(
        "/kb-admin/docs/new",
        data={"csrf_token": csrf(client), "description": desc},
        files={"file": (name, content, "application/octet-stream")},
        follow_redirects=False,
    )


def doc_id(client: TestClient) -> str:
    """Lê o id do primeiro documento da tabela. Entrada: cliente. Saída: id."""
    match = re.search(r"/kb-admin/docs/([0-9a-f]{16})/download", client.get("/kb-admin/docs").text)
    assert match is not None
    return match.group(1)


def test_tabs_in_header(logged_client: TestClient) -> None:
    """O cabeçalho tem as abas Manuais e Documentos de apoio."""
    page = logged_client.get("/kb-admin/docs").text
    assert "Documentos de apoio" in page and 'href="/kb-admin/"' in page and "Nenhum documento ainda." in page


def test_admin_full_flow(logged_client: TestClient) -> None:
    """Admin envia, vê, baixa, substitui, edita a descrição e apaga."""
    response = create_doc(logged_client, "Instruções.md", b"conteudo", "Manual de uso")
    assert response.status_code == 303 and response.headers["location"] == "/kb-admin/docs?done=created"
    page = logged_client.get("/kb-admin/docs?done=created").text
    assert "Documento enviado." in page and "Instruções.md" in page and "Manual de uso" in page
    identifier = doc_id(logged_client)

    download = logged_client.get(f"/kb-admin/docs/{identifier}/download")
    assert download.content == b"conteudo"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "filename*=utf-8''Instru%C3%A7%C3%B5es.md" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"

    replaced = logged_client.post(
        f"/kb-admin/docs/{identifier}/replace",
        data={"csrf_token": csrf(logged_client)},
        files={"file": ("v2.md", b"novo", "text/markdown")},
        follow_redirects=False,
    )
    assert replaced.headers["location"] == "/kb-admin/docs?done=replaced"
    assert logged_client.get(f"/kb-admin/docs/{identifier}/download").content == b"novo"

    edited = logged_client.post(
        f"/kb-admin/docs/{identifier}/edit",
        data={"csrf_token": csrf(logged_client), "description": "Outra descrição"},
        follow_redirects=False,
    )
    assert edited.headers["location"] == "/kb-admin/docs?done=updated"
    assert "Outra descrição" in logged_client.get("/kb-admin/docs").text

    assert "Apagar v2.md" in logged_client.get(f"/kb-admin/docs/{identifier}/delete").text
    deleted = logged_client.post(f"/kb-admin/docs/{identifier}/delete", data={"csrf_token": csrf(logged_client)})
    assert "Documento apagado." in deleted.text and "Nenhum documento ainda." in deleted.text


def test_form_errors_are_shown(logged_client: TestClient) -> None:
    """Descrição vazia e nome repetido voltam ao formulário com a mensagem."""
    empty = create_doc(logged_client, desc=" ")
    assert empty.status_code == 400 and "Informe uma descrição." in empty.text
    create_doc(logged_client)
    duplicate = create_doc(logged_client, "PROMPT.md")
    assert duplicate.status_code == 400 and "Substituir" in duplicate.text


def test_changes_require_csrf(logged_client: TestClient) -> None:
    """Alteração sem CSRF é recusada."""
    response = logged_client.post(
        "/kb-admin/docs/new", data={"csrf_token": "errado", "description": "x"}, files={"file": ("a.md", b"a")}
    )
    assert response.status_code == 403


def test_editor_reads_but_cannot_change(logged_client: TestClient, editor_client: TestClient) -> None:
    """Editor vê e baixa, não vê botões e recebe 403 em toda alteração."""
    create_doc(logged_client)
    identifier = doc_id(logged_client)
    page = editor_client.get("/kb-admin/docs").text
    assert "prompt.md" in page and "Enviar documento" not in page and "Substituir" not in page
    assert editor_client.get(f"/kb-admin/docs/{identifier}/download").status_code == 200
    token = csrf(editor_client)
    assert editor_client.get("/kb-admin/docs/new").status_code == 403
    assert create_doc(editor_client).status_code == 403
    for action in ("replace", "edit", "delete"):
        assert editor_client.get(f"/kb-admin/docs/{identifier}/{action}").status_code == 403
    assert editor_client.post(f"/kb-admin/docs/{identifier}/delete", data={"csrf_token": token}).status_code == 403
    assert editor_client.post(
        f"/kb-admin/docs/{identifier}/edit", data={"csrf_token": token, "description": "x"}
    ).status_code == 403
    assert editor_client.post(
        f"/kb-admin/docs/{identifier}/replace", data={"csrf_token": token}, files={"file": ("z.md", b"z")}
    ).status_code == 403
    assert "prompt.md" in logged_client.get("/kb-admin/docs").text


def test_unknown_document_is_404(logged_client: TestClient) -> None:
    """Documento inexistente responde 404."""
    assert logged_client.get("/kb-admin/docs/0123456789abcdef/download").status_code == 404
    assert logged_client.get("/kb-admin/docs/nao-e-id/download").status_code == 404
