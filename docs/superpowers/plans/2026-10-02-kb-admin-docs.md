# kb_admin: documentos de apoio e perfil somente leitura — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aba "Documentos de apoio" com CRUD de arquivos e descrição, o prompt `PDF_TO_RAG.md` movido para dentro do app como conteúdo inicial, e o papel `editor` somente leitura no app inteiro.

**Architecture:** Novo módulo `docs_store.py` (biblioteca em `/data/admin/docs/<id>/` com `content` + `meta.json`, escrita atômica e lock global), rotas novas em `web.py` com uma verificação `require_manager`, templates novos e abas no cabeçalho. O seed vem de `docker/kb_admin/seed/` dentro da imagem.

**Tech Stack:** o mesmo do kb_admin (Python 3.12, FastAPI, Jinja2, pytest em container).

**Spec:** `docs/superpowers/specs/2026-10-02-kb-admin-docs-design.md` (adendo de `docs/superpowers/specs/2026-10-01-kb-admin-design.md`)

## Global Constraints

- Identificadores em inglês, docstring em português em toda função, textos de tela em português.
- Papéis: `ALLOWED_ROLES = {owner, admin, editor}` para entrar; `MANAGER_ROLES = {owner, admin}` para alterar. Mensagem 403: "Seu papel no Dify (<papel>) permite só consulta."
- Documentos: qualquer tipo, até 50 MB; descrição obrigatória, até 500 caracteres, espaços e quebras normalizados para um espaço; nome até 200 caracteres, sem caracteres de controle, sem `.`/`..`; nome único sem diferenciar maiúsculas.
- id: `secrets.token_hex(8)`, rotas aceitam só `^[0-9a-f]{16}$`.
- Download sempre como anexo, com `X-Content-Type-Options: nosniff`.
- Seed: `docker/kb_admin/seed/PDF_TO_RAG.md`, descrição "Prompt de conversão PDF → Markdown (usar na LLM externa para gerar o .zip do manual)", autor "conteúdo inicial", marcador `/data/admin/docs/.seeded`.
- Testes: `docker/kb_admin/run_tests.sh`. O hook do repositório bloqueia `git commit` em comandos que contenham `-n`: commit em comando separado.
- Commits em português com os trailers `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` e `Claude-Session: https://claude.ai/code/session_01UUf423QAqYdWBSb3RFWrAM`.

## Review Focus

- Editor monta à mão um POST de alteração (manual ou documento) com CSRF válido: deve receber 403 e nada muda. Teste em Task 1 e Task 3.
- Arquivo enviado chamado `meta.json` ou `../x`: não pode corromper metadados nem sair da pasta. Teste em Task 2.
- Nome com acento (`Instruções.pdf`) no download: o navegador precisa receber o nome certo. Teste em Task 3.
- Upload acima de 50 MB: mensagem clara, sem sobra de pasta temporária. Teste em Task 2.
- Apagar o prompt inicial e reiniciar o serviço: ele não pode voltar. Teste em Task 2.

---

### Task 1: Perfil somente leitura para o editor

**Files:**
- Modify: `docker/kb_admin/kb_admin/auth.py` (constante `MANAGER_ROLES`, propriedade `UserSession.can_manage`)
- Modify: `docker/kb_admin/kb_admin/errors.py` (`NotFoundError` base; `ManualNotFoundError` herda dela)
- Modify: `docker/kb_admin/kb_admin/web.py` (`ForbiddenError`, `require_manager`, handler 403, aplicar nas rotas de alteração de manuais; handler de domínio usa `NotFoundError`)
- Modify: templates `list.html` e `manual.html` (botões só com `user.can_manage`)
- Modify: `docker/kb_admin/tests/conftest.py` (ana vira `admin`; novo usuário `edu@camara.rj` com papel `editor`; fixture `editor_client`)
- Test: `docker/kb_admin/tests/test_web_roles.py`

**Interfaces:**
- Produces: `auth.MANAGER_ROLES`, `UserSession.can_manage -> bool`, `errors.NotFoundError`, `web.ForbiddenError(role: str)`, fixture `editor_client`.

- [ ] **Step 1: conftest com usuário editor**

Em `FakeAuthClient.authenticate`, ana passa a devolver `"admin"` e `edu@camara.rj` com a mesma senha devolve `"editor"`. Nova fixture:

```python
@pytest.fixture
def editor_client(settings: Settings, store: ManualStore, docs: DocumentStore) -> TestClient:
    """Cliente logado como editor (somente leitura). Saída: TestClient."""
    client = TestClient(create_app(settings, store, docs, FakeAuthClient()))
    response = client.post(
        "/kb-admin/login", data={"email": "edu@camara.rj", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client
```

(A fixture `docs` e o parâmetro novo de `create_app` entram na Task 3; nesta task, `editor_client` chama `create_app(settings, store, FakeAuthClient())` e a Task 3 acrescenta `docs`.)

- [ ] **Step 2: testes de papel (falhando)**

**File:** `docker/kb_admin/tests/test_web_roles.py`
```python
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
```

- [ ] **Step 3:** rodar `docker/kb_admin/run_tests.sh tests/test_web_roles.py -q` — Expected: FAIL (editor ainda altera / vê botões).

- [ ] **Step 4: implementar**

`auth.py`: `MANAGER_ROLES = frozenset({"owner", "admin"})` e, em `UserSession`:
```python
    @property
    def can_manage(self) -> bool:
        """Diz se o papel pode alterar manuais e documentos. Entrada: nenhuma. Saída: bool."""
        return self.role in MANAGER_ROLES
```

`errors.py`: `class NotFoundError(KbAdminError)` ("Recurso inexistente (manual ou documento).") e `ManualNotFoundError(NotFoundError)`.

`web.py`:
```python
class ForbiddenError(Exception):
    """O papel do usuário só permite consulta."""

    def __init__(self, role: str) -> None:
        """Guarda o papel. Entrada: papel no Dify. Saída: nenhuma."""
        super().__init__(role)
        self.role = role
```
Dentro de `create_app`:
```python
    def require_manager(user: UserSession) -> None:
        """Bloqueia alterações para quem só consulta. Entrada: sessão. Saída: nenhuma; ForbiddenError se for editor."""
        if not user.can_manage:
            raise ForbiddenError(user.role)

    @app.exception_handler(ForbiddenError)
    async def handle_forbidden(request: Request, exc: ForbiddenError) -> Response:
        """Recusa alteração de quem só consulta. Entrada: requisição e erro. Saída: página de erro 403."""
        user = codec.load(request.cookies.get(SESSION_COOKIE))
        message = f"Seu papel no Dify ({exc.role}) permite só consulta."
        return render(request, "error.html", {"message": message, "user": user}, 403)
```
`require_manager(user)` logo no início de `upload_form`, `delete_form` e, depois do `check_csrf`, em `upload`, `confirm_upload`, `cancel_upload` e `delete`. No handler de domínio, `status = 404 if isinstance(exc, NotFoundError) else 400`.

Templates: em `list.html`, o parágrafo do botão "Enviar manual" fica dentro de `{% if user.can_manage %}`; em `manual.html`, os links "Substituir" e "Apagar" ficam dentro de `{% if user.can_manage %}`.

- [ ] **Step 5:** `docker/kb_admin/run_tests.sh -q` — Expected: tudo passa.
- [ ] **Step 6:** commit "Torna o papel editor somente leitura no kb_admin".

---

### Task 2: Biblioteca de documentos (armazenamento)

**Files:**
- Create: `docker/kb_admin/kb_admin/docs_store.py`
- Modify: `docker/kb_admin/kb_admin/errors.py` (`DocumentError`, `DocumentNotFoundError(NotFoundError)`)
- Test: `docker/kb_admin/tests/test_docs_store.py`

**Interfaces:**
- Produces: `SupportDocument(id, filename, description, size, updated_at, updated_by)`; `DocumentStore(docs_dir: Path, clock=utc_now)` com `ensure_dirs()`, `cleanup_temporary()`, `list_documents() -> list[SupportDocument]`, `get(doc_id) -> SupportDocument`, `file_path(doc_id) -> Path`, `create(source: BinaryIO, filename: str, description: str, user_email: str) -> SupportDocument`, `replace(doc_id, source, filename, user_email) -> SupportDocument`, `update_description(doc_id, description, user_email) -> SupportDocument`, `delete(doc_id)`, `seed(seed_file: Path, description: str, author: str) -> bool`; constantes `MAX_DOCUMENT_BYTES`, `SEED_DESCRIPTION`, `SEED_AUTHOR`.

- [ ] **Step 1: testes (falhando)**

**File:** `docker/kb_admin/tests/test_docs_store.py`
```python
"""Testes da biblioteca de documentos de apoio."""
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kb_admin.docs_store import DocumentStore
from kb_admin.errors import DocumentError, DocumentNotFoundError

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def docs(tmp_path: Path) -> DocumentStore:
    """Biblioteca em pasta temporária. Saída: DocumentStore."""
    store = DocumentStore(tmp_path / "docs", clock=lambda: NOW)
    store.ensure_dirs()
    return store


def add(docs: DocumentStore, name: str = "prompt.md", content: bytes = b"conteudo", desc: str = "Prompt") -> str:
    """Cria um documento. Entrada: biblioteca, nome, conteúdo e descrição. Saída: id."""
    return docs.create(io.BytesIO(content), name, desc, "ana@camara.rj").id


def test_create_and_read(docs: DocumentStore) -> None:
    """Criar grava conteúdo e metadados."""
    doc_id = add(docs, "Instruções.pdf", b"%PDF", "  Manual   de\nuso  ")
    doc = docs.get(doc_id)
    assert (doc.filename, doc.description, doc.size, doc.updated_by) == ("Instruções.pdf", "Manual de uso", 4, "ana@camara.rj")
    assert doc.updated_at == NOW
    assert docs.file_path(doc_id).read_bytes() == b"%PDF"
    assert [d.id for d in docs.list_documents()] == [doc_id]


def test_list_is_alphabetical(docs: DocumentStore) -> None:
    """A lista sai em ordem alfabética, sem diferenciar maiúsculas."""
    add(docs, "b.md")
    add(docs, "A.md")
    assert [d.filename for d in docs.list_documents()] == ["A.md", "b.md"]


def test_duplicate_name_is_refused(docs: DocumentStore) -> None:
    """Nome repetido (sem diferenciar maiúsculas) é recusado."""
    add(docs, "Prompt.md")
    with pytest.raises(DocumentError, match="Substituir"):
        add(docs, "prompt.md")


@pytest.mark.parametrize("name", ["", ".", "..", "a\x00b.md", "x" * 201 + ".md"])
def test_invalid_names(docs: DocumentStore, name: str) -> None:
    """Nomes vazios, especiais, com controle ou longos demais são recusados."""
    with pytest.raises(DocumentError):
        add(docs, name)


def test_path_is_stripped_and_meta_json_is_safe(docs: DocumentStore, tmp_path: Path) -> None:
    """Caminho no nome é descartado; um arquivo chamado meta.json não corrompe os metadados."""
    doc_id = add(docs, "..\\..\\meta.json", b'{"x": 1}')
    assert docs.get(doc_id).filename == "meta.json"
    assert docs.file_path(doc_id).read_bytes() == b'{"x": 1}'
    assert not (tmp_path / "meta.json").exists()


def test_description_rules(docs: DocumentStore) -> None:
    """Descrição vazia ou longa demais é recusada."""
    with pytest.raises(DocumentError, match="descrição"):
        add(docs, desc="   ")
    with pytest.raises(DocumentError, match="500"):
        add(docs, desc="x" * 501)


def test_size_limit_leaves_nothing(docs: DocumentStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Arquivo acima do limite é recusado sem deixar pasta temporária."""
    monkeypatch.setattr("kb_admin.docs_store.MAX_DOCUMENT_BYTES", 3)
    with pytest.raises(DocumentError, match="50 MB"):
        add(docs, content=b"1234")
    assert [p.name for p in (tmp_path / "docs").iterdir() if not p.name.startswith(".lock")] == []


def test_replace_keeps_description_and_can_rename(docs: DocumentStore) -> None:
    """Substituir troca o arquivo e o nome e mantém a descrição."""
    doc_id = add(docs, "v1.md", b"um", "Prompt oficial")
    doc = docs.replace(doc_id, io.BytesIO(b"dois!"), "v2.md", "bia@camara.rj")
    assert (doc.filename, doc.description, doc.size, doc.updated_by) == ("v2.md", "Prompt oficial", 5, "bia@camara.rj")
    assert docs.file_path(doc_id).read_bytes() == b"dois!"


def test_replace_refuses_name_of_other_document(docs: DocumentStore) -> None:
    """Substituir não pode assumir o nome de outro documento."""
    add(docs, "a.md")
    doc_id = add(docs, "b.md")
    with pytest.raises(DocumentError):
        docs.replace(doc_id, io.BytesIO(b"x"), "A.md", "ana@camara.rj")
    assert docs.get(doc_id).filename == "b.md"


def test_update_description(docs: DocumentStore) -> None:
    """Editar a descrição mantém o arquivo."""
    doc_id = add(docs)
    assert docs.update_description(doc_id, "Nova descrição", "bia@camara.rj").description == "Nova descrição"
    assert docs.file_path(doc_id).read_bytes() == b"conteudo"


def test_delete_and_not_found(docs: DocumentStore) -> None:
    """Apagar remove o documento; ids inválidos ou inexistentes dão 'não encontrado'."""
    doc_id = add(docs)
    docs.delete(doc_id)
    assert docs.list_documents() == []
    for bad in (doc_id, "../etc", "ABCDEF0123456789"):
        with pytest.raises(DocumentNotFoundError):
            docs.get(bad)


def test_seed_runs_once_and_does_not_come_back(docs: DocumentStore, tmp_path: Path) -> None:
    """O conteúdo inicial entra uma vez; apagado, não volta."""
    seed = tmp_path / "PDF_TO_RAG.md"
    seed.write_text("# Prompt\n", encoding="utf-8")
    assert docs.seed(seed, "Prompt de conversão", "conteúdo inicial") is True
    [doc] = docs.list_documents()
    assert (doc.filename, doc.updated_by) == ("PDF_TO_RAG.md", "conteúdo inicial")
    docs.delete(doc.id)
    assert docs.seed(seed, "Prompt de conversão", "conteúdo inicial") is False
    assert docs.list_documents() == []


def test_seed_missing_file_waits(docs: DocumentStore, tmp_path: Path) -> None:
    """Sem o arquivo de seed, nada é marcado e ele entra quando existir."""
    seed = tmp_path / "PDF_TO_RAG.md"
    assert docs.seed(seed, "d", "a") is False
    seed.write_text("x", encoding="utf-8")
    assert docs.seed(seed, "d", "a") is True


def test_cleanup_removes_leftovers(docs: DocumentStore, tmp_path: Path) -> None:
    """Sobras temporárias somem na limpeza."""
    (tmp_path / "docs/.new.tmp-abc").mkdir()
    (tmp_path / "docs/.0123456789abcdef.old-abc").mkdir()
    docs.cleanup_temporary()
    assert sorted(p.name for p in (tmp_path / "docs").iterdir() if p.is_dir()) == []
```

- [ ] **Step 2:** rodar — Expected: FAIL (`No module named 'kb_admin.docs_store'`).

- [ ] **Step 3: implementar `docs_store.py` e os erros**

`errors.py`:
```python
class DocumentError(KbAdminError):
    """Documento de apoio recusado (nome, descrição ou tamanho)."""


class DocumentNotFoundError(NotFoundError):
    """Documento de apoio inexistente (ou id inválido)."""
```

**File:** `docker/kb_admin/kb_admin/docs_store.py` — conforme as Interfaces acima, com:
- pasta por documento `<docs_dir>/<id>/` com `content` (o arquivo; o nome original fica só no `meta.json`) e `meta.json`;
- `clean_filename` (nome sem caminho; recusa vazio, `.`/`..`, caracteres de controle, mais de 200 caracteres) e `clean_description` (normaliza espaços; recusa vazia e mais de 500);
- cópia com limite de `MAX_DOCUMENT_BYTES` em pasta oculta `.new.tmp-<hex>`, removida em qualquer falha;
- lock global (`flock` em `<docs_dir>/.lock`) para checar nome único e trocar pastas;
- `replace` troca a pasta inteira (renomeia a atual para `.<id>.old-<hex>`, renomeia a nova e apaga a antiga);
- `update_description` regrava o `meta.json` com arquivo temporário + `os.replace`;
- `seed` só roda sem o marcador `.seeded` e com o arquivo existente; grava o marcador depois de criar.

- [ ] **Step 4:** rodar `docker/kb_admin/run_tests.sh -q` — Expected: tudo passa.
- [ ] **Step 5:** commit "Adiciona ao kb_admin a biblioteca de documentos de apoio".

---

### Task 3: Aba Documentos de apoio (web)

**Files:**
- Modify: `docker/kb_admin/kb_admin/web.py` (`create_app(settings, store, docs, auth_client)`, filtro `human_size`, rotas `/kb-admin/docs...`)
- Modify: `docker/kb_admin/kb_admin/templates/base.html` (abas Manuais / Documentos de apoio)
- Create: templates `docs.html`, `doc_form.html`, `doc_delete.html`
- Modify: `docker/kb_admin/tests/conftest.py` (fixture `docs`; `create_app` com `docs`)
- Test: `docker/kb_admin/tests/test_web_docs.py`

**Interfaces:**
- Consumes: `DocumentStore` (Task 2), `require_manager`/`ForbiddenError` (Task 1).
- Produces: rotas `GET /kb-admin/docs`, `GET|POST /kb-admin/docs/new`, `GET /kb-admin/docs/{id}/download`, `GET|POST /kb-admin/docs/{id}/replace`, `GET|POST /kb-admin/docs/{id}/edit`, `GET|POST /kb-admin/docs/{id}/delete`.

- [ ] **Step 1: testes (falhando)**

**File:** `docker/kb_admin/tests/test_web_docs.py`
```python
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
```

- [ ] **Step 2:** rodar — Expected: FAIL (rotas inexistentes).

- [ ] **Step 3: implementar** rotas, filtro `human_size` (`"900 bytes"`, `"12,3 KB"`, `"1,5 MB"`), mensagens `?done=` (`created` → "Documento enviado.", `replaced` → "Arquivo substituído.", `updated` → "Descrição atualizada.", `deleted` → "Documento apagado."), download com `FileResponse(path, filename=..., media_type=mimetypes.guess_type(...) or "application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})`, `DocumentError` volta ao formulário com status 400, abas no `base.html` (variável `nav`: `"manuals"` ou `"docs"`).

- [ ] **Step 4:** `docker/kb_admin/run_tests.sh -q` — Expected: tudo passa.
- [ ] **Step 5:** commit "Adiciona ao kb_admin a aba Documentos de apoio".

---

### Task 4: Mover o prompt para o app (seed) e atualizar referências

**Files:**
- Move: `docker/kb_assets/docs/PDF_TO_RAG.md` → `docker/kb_admin/seed/PDF_TO_RAG.md` (`git mv`)
- Modify: `docker/kb_admin/Dockerfile` (`COPY seed ./seed`), `docker/kb_admin/.dockerignore` (sem mudança se não ignorar `seed`)
- Modify: `docker/kb_admin/kb_admin/main.py` (cria `DocumentStore(settings.data_dir / "docs")`, `ensure_dirs`, `cleanup_temporary`, `seed(SEED_FILE, SEED_DESCRIPTION, SEED_AUTHOR)`)
- Modify: `docker/kb_admin/tests/test_main.py` (o seed aparece na lista)
- Modify: `upload.html` (texto do PDF_TO_RAG aponta para a aba), `docker/kb_assets/README.md`, `docker/README.swarm.md`, `docker/kb_admin/README.md`, spec original (`docs/superpowers/specs/2026-10-01-kb-admin-design.md`) — trocar o caminho antigo pela aba Documentos de apoio (o arquivo-fonte fica em `docker/kb_admin/seed/`).

- [ ] **Step 1:** em `test_main.py`, depois do healthcheck, conferir que `(tmp_path / "data/docs/.seeded").exists()` e que há exatamente um documento cujo `meta.json` tem `filename == "PDF_TO_RAG.md"`. Rodar — Expected: FAIL.
- [ ] **Step 2:** `git mv`, Dockerfile, `main.py` (`SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "PDF_TO_RAG.md"`). Rodar — Expected: passa.
- [ ] **Step 3:** atualizar as referências; `grep -rn "kb_assets/docs" docker docs/superpowers/specs` não deve achar nada.
- [ ] **Step 4:** commit "Move o prompt PDF_TO_RAG para dentro do kb_admin como documento inicial".

---

### Task 5: Verificação no DEV

- [ ] `docker/kb_admin/run_tests.sh -q`, `docker/test_deploy.sh`, `docker/kb_assets/test_publish.sh` passam.
- [ ] `printf '\ns\n' | docker/deploy.sh --swarm` termina com "Dify pronto".
- [ ] Dentro do container `dify_kb_admin`: `/data/admin/docs/.seeded` existe e a biblioteca tem `PDF_TO_RAG.md`; `GET /kb-admin/healthz` responde `ok`.
- [ ] Relatar ao usuário o que testar no navegador (aba, download, papel editor).
