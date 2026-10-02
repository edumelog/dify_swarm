"""Login com as credenciais do Dify e cookie de sessão próprio do kb_admin."""
from __future__ import annotations

import base64
import secrets
from dataclasses import asdict, dataclass
from http.cookies import CookieError, SimpleCookie

import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer

from kb_admin.errors import AuthError

ALLOWED_ROLES = frozenset({"owner", "admin", "editor"})
LOGIN_PATH = "/console/api/login"
WORKSPACE_PATH = "/console/api/workspaces/current"
ACCESS_COOKIE = "access_token"
CSRF_COOKIE = "csrf_token"
HOST_PREFIX = "__Host-"
SESSION_SALT = "kb-admin-session"
MSG_INVALID = "E-mail ou senha inválidos."
MSG_UNAVAILABLE = "Não foi possível falar com o Dify. Tente novamente em instantes."
LOGIN_ERRORS = {
    "account_banned": "Conta bloqueada no Dify.",
    "account_in_freeze": "Conta desativada no Dify.",
}


@dataclass(frozen=True)
class UserSession:
    """Usuário logado no kb_admin e o token CSRF dos formulários."""

    email: str
    role: str
    csrf_token: str


def new_session(email: str, role: str) -> UserSession:
    """Cria a sessão de um login. Entrada: e-mail e papel. Saída: UserSession com CSRF novo."""
    return UserSession(email=email, role=role, csrf_token=secrets.token_urlsafe(32))


class SessionCodec:
    """Assina e lê o cookie de sessão (itsdangerous, com validade)."""

    def __init__(self, secret_key: str, max_age_seconds: int) -> None:
        """Configura o assinador. Entrada: segredo e validade em segundos. Saída: nenhuma."""
        self._serializer = URLSafeTimedSerializer(secret_key, salt=SESSION_SALT)
        self._max_age = max_age_seconds

    def dump(self, session: UserSession) -> str:
        """Gera o valor do cookie. Entrada: sessão. Saída: texto assinado."""
        return self._serializer.dumps(asdict(session))

    def load(self, value: str | None) -> UserSession | None:
        """Lê o cookie. Entrada: valor ou None. Saída: UserSession, ou None se ausente, adulterado ou expirado."""
        if not value:
            return None
        try:
            data = self._serializer.loads(value, max_age=self._max_age)
            return UserSession(email=data["email"], role=data["role"], csrf_token=data["csrf_token"])
        except (BadSignature, KeyError, TypeError):
            return None


def _cookies(response: httpx.Response) -> dict[str, str]:
    """Lê os Set-Cookie da resposta. Entrada: resposta do login. Saída: {nome: valor}."""
    values: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        try:
            parsed.load(header)
        except CookieError:
            continue
        values.update({morsel.key: morsel.value for morsel in parsed.values()})
    return values


def _find_cookie(values: dict[str, str], name: str) -> tuple[str, str]:
    """Acha o cookie com ou sem __Host-. Entrada: cookies e nome base. Saída: (nome real, valor); AuthError se faltar."""
    for real_name in (f"{HOST_PREFIX}{name}", name):
        if real_name in values:
            return real_name, values[real_name]
    raise AuthError(MSG_UNAVAILABLE)


def _json(response: httpx.Response) -> dict[str, object]:
    """Lê o corpo JSON sem quebrar. Entrada: resposta. Saída: dicionário (vazio se não for JSON de objeto)."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _check_login(response: httpx.Response) -> None:
    """Traduz a resposta do login. Entrada: resposta. Saída: nenhuma; AuthError se o login falhou."""
    if response.status_code == 401:
        raise AuthError(MSG_INVALID)
    if response.status_code == 429:
        raise AuthError("Muitas tentativas de login com senha errada. Aguarde alguns minutos e tente de novo.")
    if response.status_code == 400:
        raise AuthError(LOGIN_ERRORS.get(str(_json(response).get("code", "")), MSG_INVALID))
    if response.status_code == 403:
        raise AuthError("O login por e-mail e senha está desativado no Dify.")
    if response.status_code != 200:
        raise AuthError(MSG_UNAVAILABLE)
    if _json(response).get("result") != "success":
        raise AuthError("Sua conta não pertence a nenhum workspace do Dify.")


class DifyAuthClient:
    """Confere e-mail e senha no próprio Dify e devolve o papel do usuário no workspace."""

    def __init__(self, api_url: str, transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> None:
        """Configura o acesso à API. Entrada: URL interna do Dify, transporte (testes) e timeout. Saída: nenhuma."""
        self._api_url = api_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def authenticate(self, email: str, password: str) -> str:
        """Faz login no Dify. Entrada: e-mail e senha. Saída: papel permitido; AuthError em qualquer falha."""
        payload = {
            "email": email,
            "password": base64.b64encode(password.encode()).decode(),
            "remember_me": False,
        }
        try:
            with httpx.Client(base_url=self._api_url, transport=self._transport, timeout=self._timeout) as client:
                login = client.post(LOGIN_PATH, json=payload)
                _check_login(login)
                cookies = _cookies(login)
                client.cookies.clear()
                _, access_token = _find_cookie(cookies, ACCESS_COOKIE)
                csrf_name, csrf_token = _find_cookie(cookies, CSRF_COOKIE)
                workspace = client.post(
                    WORKSPACE_PATH,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "X-CSRF-Token": csrf_token,
                        "Cookie": f"{csrf_name}={csrf_token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthError(MSG_UNAVAILABLE) from exc
        if workspace.status_code != 200:
            raise AuthError(MSG_UNAVAILABLE)
        role = str(_json(workspace).get("role", ""))
        if role not in ALLOWED_ROLES:
            raise AuthError(
                f"Sem permissão para gerenciar a base de conhecimento (seu papel no Dify: {role or 'desconhecido'})."
            )
        return role
