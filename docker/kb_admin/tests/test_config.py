"""Testes da leitura de configuração do kb_admin."""
from pathlib import Path

import pytest

from kb_admin.config import load_settings
from kb_admin.errors import ConfigError

BASE_ENV = {"APP_WEB_URL": "http://dify.dev.dti", "SECRET_KEY": "dify-secret"}


def test_base_url_defaults_to_app_web_url() -> None:
    """Sem KB_ASSETS_BASE_URL, a URL base é APP_WEB_URL + /kb-assets."""
    settings = load_settings(BASE_ENV)
    assert settings.assets_base_url == "http://dify.dev.dti/kb-assets"
    assert settings.public_host == "dify.dev.dti"


def test_explicit_base_url_wins_and_loses_trailing_slash() -> None:
    """KB_ASSETS_BASE_URL tem prioridade e perde a barra final."""
    settings = load_settings({**BASE_ENV, "KB_ASSETS_BASE_URL": "https://chat.hmg.dti/kb-assets/"})
    assert settings.assets_base_url == "https://chat.hmg.dti/kb-assets"


def test_missing_base_url_is_config_error() -> None:
    """Sem KB_ASSETS_BASE_URL nem APP_WEB_URL o app não sobe."""
    with pytest.raises(ConfigError, match="KB_ASSETS_BASE_URL ou APP_WEB_URL"):
        load_settings({"SECRET_KEY": "x"})


def test_invalid_scheme_is_config_error() -> None:
    """URL base sem http(s) é recusada."""
    with pytest.raises(ConfigError, match="inválida"):
        load_settings({**BASE_ENV, "KB_ASSETS_BASE_URL": "ftp://x/kb-assets"})


def test_secret_key_is_derived_from_dify_secret() -> None:
    """Sem KB_ADMIN_SECRET_KEY, o segredo é derivado (e diferente) do SECRET_KEY do Dify."""
    first = load_settings(BASE_ENV).secret_key
    assert first == load_settings(BASE_ENV).secret_key
    assert first != "dify-secret"


def test_explicit_secret_key_wins() -> None:
    """KB_ADMIN_SECRET_KEY é usado como está."""
    settings = load_settings({**BASE_ENV, "KB_ADMIN_SECRET_KEY": "proprio"})
    assert settings.secret_key == "proprio"


def test_missing_secrets_is_config_error() -> None:
    """Sem nenhum segredo o app não sobe."""
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        load_settings({"APP_WEB_URL": "http://dify.dev.dti"})


def test_defaults() -> None:
    """Valores padrão de diretórios, API, limites e modelos."""
    settings = load_settings(BASE_ENV)
    assert settings.assets_dir == Path("/data/kb-assets")
    assert settings.data_dir == Path("/data/admin")
    assert settings.dify_api_url == "http://api:5001"
    assert settings.child_max_length == 1000
    assert settings.max_segmentation_length == 4000
    assert settings.embedding_model_label == "text-embedding-3-small"
    assert settings.rerank_model_label == "jina-reranker-v3"


def test_limits_from_env() -> None:
    """Limites vêm do .env do Dify e do kb_admin."""
    settings = load_settings(
        {**BASE_ENV, "INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH": "3000", "KB_CHILD_MAX_LENGTH": "800"}
    )
    assert settings.max_segmentation_length == 3000
    assert settings.child_max_length == 800


@pytest.mark.parametrize("value", ["abc", "10", "5000"])
def test_invalid_child_max_length(value: str) -> None:
    """KB_CHILD_MAX_LENGTH precisa ser inteiro entre 50 e o limite do Dify."""
    with pytest.raises(ConfigError, match="KB_CHILD_MAX_LENGTH"):
        load_settings({**BASE_ENV, "KB_CHILD_MAX_LENGTH": value})
