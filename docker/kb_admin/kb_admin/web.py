"""Rotas web do kb_admin: login, lista, envio, manual, download e exclusão."""

import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kb_admin.auth import SessionCodec, UserSession, new_session
from kb_admin.config import SESSION_MAX_AGE_SECONDS, Settings
from kb_admin.errors import AuthError, KbAdminError, ManualNotFoundError
from kb_admin.package import SLUG_PATTERN
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

    return app
