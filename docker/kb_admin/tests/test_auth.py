"""Testes do login no Dify (API simulada) e do cookie de sessão."""
import base64
import json

import httpx
import pytest

from kb_admin.auth import DifyAuthClient, SessionCodec, new_session
from kb_admin.errors import AuthError

LOGIN_COOKIES = [
    ("set-cookie", "access_token=tok-acesso; Path=/; HttpOnly"),
    ("set-cookie", "refresh_token=tok-refresh; Path=/; HttpOnly"),
    ("set-cookie", "csrf_token=tok-csrf; Path=/"),
]


def make_client(
    login: httpx.Response, workspace: httpx.Response | None = None, seen: list[httpx.Request] | None = None
) -> DifyAuthClient:
    """Cria o cliente com o Dify simulado. Entrada: respostas e lista para registrar requisições. Saída: DifyAuthClient."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Responde conforme o caminho. Entrada: requisição. Saída: resposta simulada."""
        if seen is not None:
            seen.append(request)
        if request.url.path == "/console/api/login":
            return login
        return workspace or httpx.Response(200, json={"role": "editor"})

    return DifyAuthClient("http://api:5001", transport=httpx.MockTransport(handler))


def test_success_returns_role_and_forwards_tokens() -> None:
    """Login certo devolve o papel; a consulta do workspace leva token e CSRF."""
    seen: list[httpx.Request] = []
    client = make_client(httpx.Response(200, json={"result": "success"}, headers=LOGIN_COOKIES), seen=seen)
    assert client.authenticate("ana@camara.rj", "s3nh@") == "editor"
    login_body = json.loads(seen[0].content)
    assert login_body["email"] == "ana@camara.rj"
    assert base64.b64decode(login_body["password"]).decode() == "s3nh@"
    workspace = seen[1]
    assert workspace.method == "GET" and workspace.url.path == "/console/api/workspaces/current/summary"
    assert workspace.headers["authorization"] == "Bearer tok-acesso"
    assert workspace.headers["x-csrf-token"] == "tok-csrf"
    assert workspace.headers["cookie"] == "csrf_token=tok-csrf"


def test_host_prefixed_cookies_under_https() -> None:
    """Com https o Dify usa cookies __Host-; o cliente os reenvia com o mesmo nome."""
    cookies = [
        ("set-cookie", "__Host-access_token=a1; Path=/; Secure; HttpOnly"),
        ("set-cookie", "__Host-csrf_token=c1; Path=/; Secure"),
    ]
    seen: list[httpx.Request] = []
    client = make_client(httpx.Response(200, json={"result": "success"}, headers=cookies), seen=seen)
    assert client.authenticate("ana@camara.rj", "x") == "editor"
    assert seen[1].headers["authorization"] == "Bearer a1"
    assert seen[1].headers["cookie"] == "__Host-csrf_token=c1"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={"code": "authentication_failed"}), "E-mail ou senha inválidos."),
        (httpx.Response(429, json={"code": "email_code_login_limit"}), "Muitas tentativas"),
        (httpx.Response(400, json={"code": "account_banned"}), "Conta bloqueada"),
        (httpx.Response(400, json={"code": "account_in_freeze"}), "Conta desativada"),
        (httpx.Response(400, json={"code": "invalid_param"}), "E-mail ou senha inválidos."),
        (httpx.Response(200, json={"result": "fail", "data": "workspace not found"}), "workspace"),
        (httpx.Response(502), "Não foi possível falar com o Dify"),
    ],
)
def test_login_errors(response: httpx.Response, message: str) -> None:
    """Cada erro do Dify vira uma mensagem em português."""
    with pytest.raises(AuthError, match=message):
        make_client(response).authenticate("ana@camara.rj", "x")


def test_role_without_permission() -> None:
    """Papel 'normal' não entra."""
    client = make_client(
        httpx.Response(200, json={"result": "success"}, headers=LOGIN_COOKIES),
        httpx.Response(200, json={"role": "normal"}),
    )
    with pytest.raises(AuthError, match="Sem permissão"):
        client.authenticate("ana@camara.rj", "x")


def test_dify_unreachable() -> None:
    """Falha de rede vira 'Dify indisponível'."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Simula conexão recusada. Entrada: requisição. Saída: nunca retorna."""
        raise httpx.ConnectError("recusada", request=request)

    client = DifyAuthClient("http://api:5001", transport=httpx.MockTransport(handler))
    with pytest.raises(AuthError, match="Não foi possível falar com o Dify"):
        client.authenticate("ana@camara.rj", "x")


def test_session_roundtrip_tamper_and_expiry() -> None:
    """O cookie volta igual, recusa adulteração e expira."""
    codec = SessionCodec("segredo", max_age_seconds=3600)
    session = new_session("ana@camara.rj", "editor")
    value = codec.dump(session)
    assert codec.load(value) == session
    assert codec.load(value + "x") is None
    assert codec.load(None) is None
    assert SessionCodec("outro", 3600).load(value) is None
    assert SessionCodec("segredo", max_age_seconds=-1).load(value) is None
    assert len(session.csrf_token) >= 32
