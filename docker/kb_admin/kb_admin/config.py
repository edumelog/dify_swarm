"""Configuração do kb_admin lida das variáveis de ambiente (o mesmo .env do Dify)."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kb_admin.errors import ConfigError

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
DEFAULT_ASSETS_DIR = "/data/kb-assets"
DEFAULT_DATA_DIR = "/data/admin"
DEFAULT_DIFY_API_URL = "http://api:5001"
DEFAULT_CHILD_MAX_LENGTH = 1000
DEFAULT_MAX_SEGMENTATION_LENGTH = 4000
DIFY_MIN_CHUNK_LENGTH = 50
DEFAULT_EMBEDDING_MODEL_LABEL = "text-embedding-3-small"
DEFAULT_RERANK_MODEL_LABEL = "jina-reranker-v3"


@dataclass(frozen=True)
class Settings:
    """Configuração resolvida do kb_admin."""

    assets_dir: Path
    data_dir: Path
    assets_base_url: str
    dify_api_url: str
    secret_key: str
    child_max_length: int
    max_segmentation_length: int
    embedding_model_label: str
    rerank_model_label: str

    @property
    def public_host(self) -> str:
        """Domínio (host[:porta]) da URL base das imagens. Saída: ex. 'dify.dev.dti'."""
        return urlsplit(self.assets_base_url).netloc


def _value(env: Mapping[str, str], name: str) -> str:
    """Lê uma variável sem espaços nas pontas. Entrada: ambiente e nome. Saída: valor ou ''."""
    return env.get(name, "").strip()


def resolve_assets_base_url(env: Mapping[str, str]) -> str:
    """Define a URL base das imagens. Entrada: ambiente. Saída: URL sem barra final; ConfigError se inválida."""
    base = _value(env, "KB_ASSETS_BASE_URL")
    if not base:
        web_url = _value(env, "APP_WEB_URL")
        if not web_url:
            raise ConfigError("Defina KB_ASSETS_BASE_URL ou APP_WEB_URL no .env.")
        base = web_url.rstrip("/") + "/kb-assets"
    base = base.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"URL base das imagens inválida: {base!r} (use http:// ou https://).")
    return base


def resolve_secret_key(env: Mapping[str, str]) -> str:
    """Escolhe o segredo do cookie. Entrada: ambiente. Saída: KB_ADMIN_SECRET_KEY ou derivado do SECRET_KEY."""
    explicit = _value(env, "KB_ADMIN_SECRET_KEY")
    if explicit:
        return explicit
    dify_secret = _value(env, "SECRET_KEY")
    if not dify_secret:
        raise ConfigError("Defina KB_ADMIN_SECRET_KEY ou SECRET_KEY no .env.")
    return hashlib.sha256(f"kb_admin:{dify_secret}".encode()).hexdigest()


def _int_value(env: Mapping[str, str], name: str, default: int) -> int:
    """Lê um inteiro opcional. Entrada: ambiente, nome e padrão. Saída: inteiro; ConfigError se não for número."""
    raw = _value(env, name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} precisa ser um número inteiro (recebido: {raw!r}).") from exc


def load_settings(env: Mapping[str, str]) -> Settings:
    """Monta a configuração. Entrada: variáveis de ambiente. Saída: Settings; ConfigError se algo faltar."""
    max_segmentation = _int_value(env, "INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH", DEFAULT_MAX_SEGMENTATION_LENGTH)
    child_max = _int_value(env, "KB_CHILD_MAX_LENGTH", DEFAULT_CHILD_MAX_LENGTH)
    if not DIFY_MIN_CHUNK_LENGTH <= child_max <= max_segmentation:
        raise ConfigError(
            f"KB_CHILD_MAX_LENGTH precisa ficar entre {DIFY_MIN_CHUNK_LENGTH} e {max_segmentation} "
            f"(recebido: {child_max})."
        )
    return Settings(
        assets_dir=Path(_value(env, "KB_ASSETS_DIR") or DEFAULT_ASSETS_DIR),
        data_dir=Path(_value(env, "KB_ADMIN_DATA_DIR") or DEFAULT_DATA_DIR),
        assets_base_url=resolve_assets_base_url(env),
        dify_api_url=(_value(env, "DIFY_API_URL") or DEFAULT_DIFY_API_URL).rstrip("/"),
        secret_key=resolve_secret_key(env),
        child_max_length=child_max,
        max_segmentation_length=max_segmentation,
        embedding_model_label=_value(env, "KB_EMBEDDING_MODEL_LABEL") or DEFAULT_EMBEDDING_MODEL_LABEL,
        rerank_model_label=_value(env, "KB_RERANK_MODEL_LABEL") or DEFAULT_RERANK_MODEL_LABEL,
    )
