"""Rotas web do kb_admin: login, lista, envio, manual, download e exclusão."""

import logging
import mimetypes
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kb_admin.auth import SessionCodec, UserSession, new_session
from kb_admin.config import SESSION_MAX_AGE_SECONDS, Settings
from kb_admin.docs_store import DocumentStore, SupportDocument
from kb_admin.errors import AuthError, DocumentError, KbAdminError, ManualNotFoundError, NotFoundError, PackageError
from kb_admin.ingest_params import IngestRecommendation, ParameterRow, parameter_rows, recommend
from kb_admin.markdown_rules import rewrite_image_urls
from kb_admin.package import SLUG_PATTERN, UPLOADED_ZIP, ManualPackage, read_package, save_upload
from kb_admin.storage import ManualStore

PREFIX = "/kb-admin"
SESSION_COOKIE = "kb_admin_session"
LOCAL_TIMEZONE = ZoneInfo("America/Sao_Paulo")
TEMPLATES_DIR = Path(__file__).parent / "templates"
DOC_MESSAGES = {
    "created": "Documento enviado.",
    "replaced": "Arquivo substituído.",
    "updated": "Descrição atualizada.",
    "deleted": "Documento apagado.",
}
logger = logging.getLogger("kb_admin")


class AuthClient(Protocol):
    """Quem confere as credenciais (o Dify, em produção)."""

    def authenticate(self, email: str, password: str) -> str:
        """Confere o login. Entrada: e-mail e senha. Saída: papel; AuthError se falhar."""
        ...


class LoginRequiredError(Exception):
    """A rota exige sessão; vira redirecionamento para o login."""


class CsrfError(Exception):
    """Formulário sem o token CSRF da sessão."""


class ForbiddenError(Exception):
    """O papel do usuário só permite consulta."""

    def __init__(self, role: str) -> None:
        """Guarda o papel. Entrada: papel no Dify. Saída: nenhuma."""
        super().__init__(role)
        self.role = role


def format_local_datetime(value: datetime | None) -> str:
    """Formata data no horário de Brasília. Entrada: datetime com fuso ou None. Saída: 'dd/mm/aaaa hh:mm' ou ''."""
    if value is None:
        return ""
    return value.astimezone(LOCAL_TIMEZONE).strftime("%d/%m/%Y %H:%M")


def format_size(value: int) -> str:
    """Formata tamanho de arquivo. Entrada: bytes. Saída: ex. '900 bytes', '12,3 KB', '1,5 MB'."""
    if value < 1024:
        return f"{value} bytes"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB".replace(".", ",")
    return f"{value / (1024 * 1024):.1f} MB".replace(".", ",")


def create_app(settings: Settings, store: ManualStore, docs: DocumentStore, auth_client: AuthClient) -> FastAPI:
    """Monta o app. Entrada: configuração, manuais, documentos de apoio e cliente de login. Saída: FastAPI pronto."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters["local_datetime"] = format_local_datetime
    templates.env.filters["human_size"] = format_size
    templates.env.globals.update(
        prefix=PREFIX, assets_base_url=settings.assets_base_url, public_host=settings.public_host
    )
    codec = SessionCodec(settings.secret_key, SESSION_MAX_AGE_SECONDS)

    def render(request: Request, name: str, context: dict[str, object], status_code: int = 200) -> HTMLResponse:
        """Renderiza um template. Entrada: requisição, arquivo, contexto e status. Saída: HTMLResponse."""
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def current_user(request: Request) -> UserSession:
        """Dependência das rotas logadas. Entrada: requisição. Saída: sessão; LoginRequiredError se não houver."""
        session = codec.load(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise LoginRequiredError()
        return session

    User = Annotated[UserSession, Depends(current_user)]
    CsrfField = Annotated[str, Form()]

    def check_csrf(user: UserSession, token: str) -> None:
        """Confere o CSRF do formulário. Entrada: sessão e token enviado. Saída: nenhuma; CsrfError se não bater."""
        if not secrets.compare_digest(user.csrf_token.encode(), token.encode()):
            raise CsrfError()

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

    @app.exception_handler(LoginRequiredError)
    async def handle_login_required(request: Request, exc: LoginRequiredError) -> Response:
        """Manda para o login. Entrada: requisição e erro. Saída: redirecionamento 303."""
        return RedirectResponse(f"{PREFIX}/login", status_code=303)

    @app.exception_handler(CsrfError)
    async def handle_csrf(request: Request, exc: CsrfError) -> Response:
        """Recusa formulário inválido. Entrada: requisição e erro. Saída: página de erro 403."""
        message = "Sessão inválida ou expirada. Recarregue a página e tente de novo."
        return render(request, "error.html", {"message": message}, 403)

    @app.exception_handler(KbAdminError)
    async def handle_domain_error(request: Request, exc: KbAdminError) -> Response:
        """Mostra erros de domínio. Entrada: requisição e erro. Saída: página de erro 404 ou 400."""
        status = 404 if isinstance(exc, NotFoundError) else 400
        user = codec.load(request.cookies.get(SESSION_COOKIE))
        return render(request, "error.html", {"message": exc.message, "user": user}, status)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        """Registra falhas inesperadas sem expor detalhes. Entrada: requisição e erro. Saída: página de erro 500."""
        logger.exception("Erro inesperado em %s", request.url.path)
        return render(request, "error.html", {"message": "Erro inesperado; tente novamente."}, 500)

    @app.get(f"{PREFIX}/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        """Healthcheck sem login. Entrada: nenhuma. Saída: 'ok'."""
        return "ok"

    @app.get(PREFIX, include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        """Acrescenta a barra final. Entrada: nenhuma. Saída: redirecionamento para /kb-admin/."""
        return RedirectResponse(f"{PREFIX}/", status_code=307)

    @app.get(f"{PREFIX}/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        """Tela de login. Entrada: requisição. Saída: formulário, ou a lista se já houver sessão."""
        if codec.load(request.cookies.get(SESSION_COOKIE)) is not None:
            return RedirectResponse(f"{PREFIX}/", status_code=303)
        return render(request, "login.html", {"error": None, "email": ""})

    @app.post(f"{PREFIX}/login")
    def login(request: Request, email: Annotated[str, Form()], password: Annotated[str, Form()]) -> Response:
        """Confere as credenciais no Dify. Entrada: e-mail e senha. Saída: cookie e redirecionamento, ou erro 401."""
        normalized = email.strip().lower()
        try:
            role = auth_client.authenticate(normalized, password)
        except AuthError as exc:
            return render(request, "login.html", {"error": exc.message, "email": email}, 401)
        response = RedirectResponse(f"{PREFIX}/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            codec.dump(new_session(normalized, role)),
            max_age=SESSION_MAX_AGE_SECONDS,
            path=PREFIX,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @app.post(f"{PREFIX}/logout")
    def logout(user: User, csrf_token: CsrfField) -> Response:
        """Encerra a sessão. Entrada: sessão e CSRF. Saída: redirecionamento ao login sem cookie."""
        check_csrf(user, csrf_token)
        response = RedirectResponse(f"{PREFIX}/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path=PREFIX)
        return response

    @app.get(f"{PREFIX}/", response_class=HTMLResponse)
    def manual_list(request: Request, user: User, deleted: str | None = None) -> Response:
        """Lista os manuais. Entrada: sessão e slug apagado (opcional). Saída: página da lista."""
        deleted_slug = deleted if deleted and SLUG_PATTERN.match(deleted) else None
        context = {"user": user, "manuals": store.list_manuals(), "deleted": deleted_slug}
        return render(request, "list.html", context)

    def slug_url(slug: str) -> str:
        """URL pública das imagens do manual. Entrada: slug. Saída: URL sem barra final."""
        return f"{settings.assets_base_url}/{slug}"

    def recommendation_for(slug: str, markdown: str) -> IngestRecommendation:
        """Recomendação sobre o .md convertido. Entrada: slug e .md original. Saída: IngestRecommendation."""
        return recommend(
            rewrite_image_urls(markdown, slug_url(slug)), settings.child_max_length, settings.max_segmentation_length
        )

    def rows_for(recommendation: IngestRecommendation) -> list[ParameterRow]:
        """Tabela de parâmetros. Entrada: recomendação. Saída: linhas com os modelos configurados."""
        return parameter_rows(recommendation, settings.embedding_model_label, settings.rerank_model_label)

    def confirm_context(user: UserSession, token: str, package: ManualPackage) -> dict[str, object]:
        """Monta a tela de substituição. Entrada: sessão, token e pacote. Saída: contexto do template."""
        legacy = store.is_legacy(package.slug)
        old_values: dict[tuple[str, str], str] = {}
        if not legacy:
            old_rows = rows_for(recommendation_for(package.slug, store.get_markdown(package.slug)))
            old_values = {(row.group, row.name): row.value for row in old_rows}
        new_rows = rows_for(recommendation_for(package.slug, package.markdown))
        changed = any(old_values.get((row.group, row.name), row.value) != row.value for row in new_rows)
        return {
            "user": user,
            "token": token,
            "package": package,
            "diff": store.diff(package),
            "rows": new_rows,
            "old_values": old_values,
            "legacy": legacy,
            "params_changed": changed,
        }

    @app.get(f"{PREFIX}/upload", response_class=HTMLResponse)
    def upload_form(request: Request, user: User, slug: str | None = None) -> Response:
        """Tela de envio. Entrada: sessão e slug a substituir (opcional). Saída: formulário."""
        require_manager(user)
        target = slug if slug and SLUG_PATTERN.match(slug) else None
        return render(request, "upload.html", {"user": user, "slug": target, "problems": [], "error": None})

    @app.post(f"{PREFIX}/upload", response_class=HTMLResponse)
    def upload(request: Request, user: User, csrf_token: CsrfField, file: Annotated[UploadFile, File()]) -> Response:
        """Recebe o zip. Entrada: sessão, CSRF e arquivo. Saída: manual publicado, confirmação ou lista de problemas."""
        check_csrf(user, csrf_token)
        require_manager(user)
        token, staging = store.new_staging()
        try:
            zip_path = staging / UPLOADED_ZIP
            save_upload(file.file, zip_path)
            package = read_package(zip_path, file.filename or "", staging)
            zip_path.unlink()
        except PackageError as exc:
            store.discard(token)
            context = {"user": user, "slug": None, "problems": exc.problems, "error": exc.message}
            return render(request, "upload.html", context, 400)
        except Exception:
            store.discard(token)
            raise
        if store.exists(package.slug):
            store.save_pending(token, package, user.email)
            return render(request, "confirm.html", confirm_context(user, token, package))
        try:
            store.publish(package, user.email)
        finally:
            store.discard(token)
        return RedirectResponse(f"{PREFIX}/manuals/{package.slug}?published=1", status_code=303)

    @app.post(f"{PREFIX}/upload/{{token}}/confirm")
    def confirm_upload(token: str, user: User, csrf_token: CsrfField) -> Response:
        """Publica a substituição. Entrada: token, sessão e CSRF. Saída: redirecionamento à tela do manual."""
        check_csrf(user, csrf_token)
        require_manager(user)
        package = store.load_pending(token, user.email)
        store.publish(package, user.email)
        store.discard(token)
        return RedirectResponse(f"{PREFIX}/manuals/{package.slug}?published=1", status_code=303)

    @app.post(f"{PREFIX}/upload/{{token}}/cancel")
    def cancel_upload(token: str, user: User, csrf_token: CsrfField) -> Response:
        """Descarta a substituição. Entrada: token, sessão e CSRF. Saída: redirecionamento à lista."""
        check_csrf(user, csrf_token)
        require_manager(user)
        store.discard(token)
        return RedirectResponse(f"{PREFIX}/", status_code=303)

    @app.get(f"{PREFIX}/manuals/{{slug}}", response_class=HTMLResponse)
    def manual_page(request: Request, slug: str, user: User, published: int = 0) -> Response:
        """Tela do manual. Entrada: slug, sessão e flag de recém-publicado. Saída: galeria, parâmetros e ações."""
        if not store.exists(slug):
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        legacy = store.is_legacy(slug)
        context: dict[str, object] = {
            "user": user,
            "slug": slug,
            "legacy": legacy,
            "images": store.published_images(slug),
            "slug_url": slug_url(slug),
            "published": bool(published),
            "meta": None,
            "recommendation": None,
            "rows": [],
        }
        if not legacy:
            recommendation = recommendation_for(slug, store.get_markdown(slug))
            context.update(meta=store.get_meta(slug), recommendation=recommendation, rows=rows_for(recommendation))
        return render(request, "manual.html", context)

    @app.get(f"{PREFIX}/manuals/{{slug}}/download")
    def download(slug: str, user: User) -> Response:
        """Baixa o .md para o Dify. Entrada: slug e sessão. Saída: <slug>.md com URLs absolutas."""
        content = rewrite_image_urls(store.get_markdown(slug), slug_url(slug))
        return Response(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{slug}.md"'},
        )

    @app.get(f"{PREFIX}/manuals/{{slug}}/delete", response_class=HTMLResponse)
    def delete_form(request: Request, slug: str, user: User) -> Response:
        """Confirmação de exclusão. Entrada: slug e sessão. Saída: página de confirmação."""
        require_manager(user)
        if not store.exists(slug):
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        return render(request, "delete.html", {"user": user, "slug": slug, "legacy": store.is_legacy(slug)})

    @app.post(f"{PREFIX}/manuals/{{slug}}/delete")
    def delete(slug: str, user: User, csrf_token: CsrfField) -> Response:
        """Apaga o manual. Entrada: slug, sessão e CSRF. Saída: redirecionamento à lista com aviso."""
        check_csrf(user, csrf_token)
        require_manager(user)
        store.delete(slug)
        return RedirectResponse(f"{PREFIX}/?deleted={slug}", status_code=303)


    def doc_form(
        request: Request, user: UserSession, mode: str, document: SupportDocument | None,
        error: str | None = None, description: str = "", status_code: int = 200,
    ) -> HTMLResponse:
        """Renderiza o formulário de documento. Entrada: modo (new/replace/edit), documento e erro. Saída: página."""
        action = f"{PREFIX}/docs/new" if document is None else f"{PREFIX}/docs/{document.id}/{mode}"
        context = {
            "user": user, "nav": "docs", "mode": mode, "document": document,
            "error": error, "description": description, "action": action,
        }
        return render(request, "doc_form.html", context, status_code)

    @app.get(f"{PREFIX}/docs", response_class=HTMLResponse)
    def doc_list(request: Request, user: User, done: str | None = None) -> Response:
        """Lista os documentos de apoio. Entrada: sessão e código da última ação. Saída: página da tabela."""
        context = {"user": user, "nav": "docs", "documents": docs.list_documents(), "message": DOC_MESSAGES.get(done or "")}
        return render(request, "docs.html", context)

    @app.get(f"{PREFIX}/docs/new", response_class=HTMLResponse)
    def doc_new_form(request: Request, user: User) -> Response:
        """Formulário de envio. Entrada: sessão. Saída: página; 403 para quem só consulta."""
        require_manager(user)
        return doc_form(request, user, "new", None)

    @app.post(f"{PREFIX}/docs/new", response_class=HTMLResponse)
    def doc_new(
        request: Request, user: User, csrf_token: CsrfField,
        file: Annotated[UploadFile, File()], description: Annotated[str, Form()] = "",
    ) -> Response:
        """Cria um documento. Entrada: sessão, CSRF, arquivo e descrição. Saída: redirecionamento ou formulário com erro."""
        check_csrf(user, csrf_token)
        require_manager(user)
        try:
            docs.create(file.file, file.filename or "", description, user.email)
        except DocumentError as exc:
            return doc_form(request, user, "new", None, exc.message, description, 400)
        return RedirectResponse(f"{PREFIX}/docs?done=created", status_code=303)

    @app.get(f"{PREFIX}/docs/{{doc_id}}/download")
    def doc_download(doc_id: str, user: User) -> Response:
        """Baixa um documento como anexo. Entrada: id e sessão. Saída: arquivo com nome original."""
        document = docs.get(doc_id)
        media_type = mimetypes.guess_type(document.filename)[0] or "application/octet-stream"
        return FileResponse(
            docs.file_path(doc_id),
            filename=document.filename,
            media_type=media_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get(f"{PREFIX}/docs/{{doc_id}}/replace", response_class=HTMLResponse)
    def doc_replace_form(request: Request, doc_id: str, user: User) -> Response:
        """Formulário de substituição. Entrada: id e sessão. Saída: página; 403 para quem só consulta."""
        require_manager(user)
        return doc_form(request, user, "replace", docs.get(doc_id))

    @app.post(f"{PREFIX}/docs/{{doc_id}}/replace", response_class=HTMLResponse)
    def doc_replace(
        request: Request, doc_id: str, user: User, csrf_token: CsrfField, file: Annotated[UploadFile, File()]
    ) -> Response:
        """Troca o arquivo de um documento. Entrada: id, sessão, CSRF e arquivo. Saída: redirecionamento ou erro."""
        check_csrf(user, csrf_token)
        require_manager(user)
        document = docs.get(doc_id)
        try:
            docs.replace(doc_id, file.file, file.filename or "", user.email)
        except DocumentError as exc:
            return doc_form(request, user, "replace", document, exc.message, status_code=400)
        return RedirectResponse(f"{PREFIX}/docs?done=replaced", status_code=303)

    @app.get(f"{PREFIX}/docs/{{doc_id}}/edit", response_class=HTMLResponse)
    def doc_edit_form(request: Request, doc_id: str, user: User) -> Response:
        """Formulário de descrição. Entrada: id e sessão. Saída: página; 403 para quem só consulta."""
        require_manager(user)
        document = docs.get(doc_id)
        return doc_form(request, user, "edit", document, description=document.description)

    @app.post(f"{PREFIX}/docs/{{doc_id}}/edit", response_class=HTMLResponse)
    def doc_edit(
        request: Request, doc_id: str, user: User, csrf_token: CsrfField, description: Annotated[str, Form()] = ""
    ) -> Response:
        """Edita a descrição. Entrada: id, sessão, CSRF e descrição. Saída: redirecionamento ou formulário com erro."""
        check_csrf(user, csrf_token)
        require_manager(user)
        document = docs.get(doc_id)
        try:
            docs.update_description(doc_id, description, user.email)
        except DocumentError as exc:
            return doc_form(request, user, "edit", document, exc.message, description, 400)
        return RedirectResponse(f"{PREFIX}/docs?done=updated", status_code=303)

    @app.get(f"{PREFIX}/docs/{{doc_id}}/delete", response_class=HTMLResponse)
    def doc_delete_form(request: Request, doc_id: str, user: User) -> Response:
        """Confirmação de exclusão. Entrada: id e sessão. Saída: página; 403 para quem só consulta."""
        require_manager(user)
        return render(request, "doc_delete.html", {"user": user, "nav": "docs", "document": docs.get(doc_id)})

    @app.post(f"{PREFIX}/docs/{{doc_id}}/delete")
    def doc_delete(doc_id: str, user: User, csrf_token: CsrfField) -> Response:
        """Apaga um documento. Entrada: id, sessão e CSRF. Saída: redirecionamento à tabela."""
        check_csrf(user, csrf_token)
        require_manager(user)
        docs.delete(doc_id)
        return RedirectResponse(f"{PREFIX}/docs?done=deleted", status_code=303)

    return app
