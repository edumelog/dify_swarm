"""Rotas web do kb_admin: login, lista, envio, manual, download e exclusão."""

import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kb_admin.auth import SessionCodec, UserSession, new_session
from kb_admin.config import SESSION_MAX_AGE_SECONDS, Settings
from kb_admin.errors import AuthError, KbAdminError, ManualNotFoundError, PackageError
from kb_admin.ingest_params import IngestRecommendation, ParameterRow, parameter_rows, recommend
from kb_admin.markdown_rules import rewrite_image_urls
from kb_admin.package import SLUG_PATTERN, UPLOADED_ZIP, ManualPackage, read_package, save_upload
from kb_admin.storage import ManualStore

PREFIX = "/kb-admin"
SESSION_COOKIE = "kb_admin_session"
LOCAL_TIMEZONE = ZoneInfo("America/Sao_Paulo")
TEMPLATES_DIR = Path(__file__).parent / "templates"
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


def format_local_datetime(value: datetime | None) -> str:
    """Formata data no horário de Brasília. Entrada: datetime com fuso ou None. Saída: 'dd/mm/aaaa hh:mm' ou ''."""
    if value is None:
        return ""
    return value.astimezone(LOCAL_TIMEZONE).strftime("%d/%m/%Y %H:%M")


def create_app(settings: Settings, store: ManualStore, auth_client: AuthClient) -> FastAPI:
    """Monta o app. Entrada: configuração, armazenamento e cliente de login. Saída: FastAPI pronto."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters["local_datetime"] = format_local_datetime
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
        status = 404 if isinstance(exc, ManualNotFoundError) else 400
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
        target = slug if slug and SLUG_PATTERN.match(slug) else None
        return render(request, "upload.html", {"user": user, "slug": target, "problems": [], "error": None})

    @app.post(f"{PREFIX}/upload", response_class=HTMLResponse)
    def upload(request: Request, user: User, csrf_token: CsrfField, file: Annotated[UploadFile, File()]) -> Response:
        """Recebe o zip. Entrada: sessão, CSRF e arquivo. Saída: manual publicado, confirmação ou lista de problemas."""
        check_csrf(user, csrf_token)
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
        package = store.load_pending(token, user.email)
        store.publish(package, user.email)
        store.discard(token)
        return RedirectResponse(f"{PREFIX}/manuals/{package.slug}?published=1", status_code=303)

    @app.post(f"{PREFIX}/upload/{{token}}/cancel")
    def cancel_upload(token: str, user: User, csrf_token: CsrfField) -> Response:
        """Descarta a substituição. Entrada: token, sessão e CSRF. Saída: redirecionamento à lista."""
        check_csrf(user, csrf_token)
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
        if not store.exists(slug):
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        return render(request, "delete.html", {"user": user, "slug": slug, "legacy": store.is_legacy(slug)})

    @app.post(f"{PREFIX}/manuals/{{slug}}/delete")
    def delete(slug: str, user: User, csrf_token: CsrfField) -> Response:
        """Apaga o manual. Entrada: slug, sessão e CSRF. Saída: redirecionamento à lista com aviso."""
        check_csrf(user, csrf_token)
        store.delete(slug)
        return RedirectResponse(f"{PREFIX}/?deleted={slug}", status_code=303)

    return app
