"""Exceções de domínio do kb_admin, com mensagens prontas para o usuário."""
from __future__ import annotations


class KbAdminError(Exception):
    """Erro de domínio cuja mensagem pode ser exibida ao usuário."""

    def __init__(self, message: str) -> None:
        """Guarda a mensagem. Entrada: texto em português. Saída: nenhuma."""
        super().__init__(message)
        self.message = message


class ConfigError(KbAdminError):
    """Configuração do ambiente inválida ou incompleta."""


class AuthError(KbAdminError):
    """Falha de login ou falta de permissão no Dify."""


class ManualNotFoundError(KbAdminError):
    """Manual inexistente (ou slug inválido)."""


class PublishInProgressError(KbAdminError):
    """Outra publicação ou exclusão do mesmo manual está em andamento."""


class PendingUploadError(KbAdminError):
    """Envio pendente inexistente, de outro usuário ou expirado."""


class PackageError(KbAdminError):
    """Pacote .zip recusado; lista todos os problemas encontrados."""

    def __init__(self, problems: list[str]) -> None:
        """Guarda os problemas. Entrada: lista de mensagens. Saída: nenhuma."""
        super().__init__("O pacote tem problemas e nada foi publicado.")
        self.problems = problems
