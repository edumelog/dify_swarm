"""Armazenamento dos manuais: imagens públicas, .md privado, envios pendentes e locks por manual."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kb_admin.errors import ManualNotFoundError, PendingUploadError, PublishInProgressError
from kb_admin.package import SLUG_PATTERN, ManualPackage

PENDING_MAX_AGE = timedelta(hours=1)
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
META_FILE = "meta.json"
PENDING_FILE = "pending.json"
PENDING_MARKDOWN = "manual.md"
FILE_MODE = 0o644
DIR_MODE = 0o755
HASH_CHUNK_BYTES = 1024 * 1024
PENDING_NOT_FOUND = "Envio não encontrado. Envie o zip de novo."


def utc_now() -> datetime:
    """Hora atual em UTC. Entrada: nenhuma. Saída: datetime com fuso."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ManualMeta:
    """Metadados de um manual publicado pela interface."""

    slug: str
    published_at: datetime
    published_by: str
    images: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManualSummary:
    """Linha da lista de manuais; legacy indica pasta publicada fora da interface."""

    slug: str
    image_count: int
    published_at: datetime | None
    published_by: str | None
    legacy: bool


@dataclass(frozen=True)
class ImageDiff:
    """Diferença de imagens entre o manual publicado e um envio novo."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        """Diz se alguma imagem entra, sai ou muda. Entrada: nenhuma. Saída: bool."""
        return bool(self.added or self.removed or self.changed)


def _sha256(path: Path) -> str:
    """Calcula o hash de um arquivo. Entrada: caminho. Saída: hex do SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _swap_dir(target: Path, replacement: Path, suffix: str) -> None:
    """Troca uma pasta pela nova e apaga a antiga. Entrada: destino, pasta nova e sufixo. Saída: nenhuma."""
    old = target.with_name(f".{target.name}.old-{suffix}")
    if target.exists():
        target.rename(old)
    replacement.rename(target)
    if old.exists():
        shutil.rmtree(old)


def _remove_dir(target: Path, suffix: str) -> None:
    """Some com a pasta de uma vez (renomeia para oculta e apaga). Entrada: pasta e sufixo. Saída: nenhuma."""
    if target.is_dir():
        old = target.with_name(f".{target.name}.old-{suffix}")
        target.rename(old)
        shutil.rmtree(old)


class ManualStore:
    """Guarda imagens (volume público), .md e metadados (volume privado) e envios pendentes."""

    def __init__(self, assets_dir: Path, data_dir: Path, clock: Callable[[], datetime] = utc_now) -> None:
        """Define as pastas. Entrada: volume público, volume privado e relógio. Saída: nenhuma."""
        self._assets_dir = assets_dir
        self._manuals_dir = data_dir / "manuals"
        self._pending_dir = data_dir / "pending"
        self._locks_dir = data_dir / "locks"
        self._clock = clock

    def ensure_dirs(self) -> None:
        """Cria as pastas de trabalho. Entrada: nenhuma. Saída: nenhuma."""
        for directory in (self._assets_dir, self._manuals_dir, self._pending_dir, self._locks_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def cleanup_temporary(self) -> None:
        """Apaga sobras de publicações interrompidas e pendentes expirados. Entrada: nenhuma. Saída: nenhuma."""
        for parent in (self._assets_dir, self._manuals_dir):
            for entry in parent.iterdir():
                if entry.name.startswith(".") and (".tmp-" in entry.name or ".old-" in entry.name):
                    shutil.rmtree(entry, ignore_errors=True)
        limit = self._clock() - PENDING_MAX_AGE
        for entry in self._pending_dir.iterdir():
            if self._pending_created_at(entry) < limit:
                shutil.rmtree(entry, ignore_errors=True)

    def _pending_created_at(self, staging: Path) -> datetime:
        """Data de criação de um pendente. Entrada: pasta. Saída: created_at do pending.json ou mtime da pasta."""
        try:
            return datetime.fromisoformat(json.loads((staging / PENDING_FILE).read_text("utf-8"))["created_at"])
        except (OSError, ValueError, KeyError):
            return datetime.fromtimestamp(staging.stat().st_mtime, timezone.utc)

    def _checked(self, slug: str) -> str:
        """Garante slug seguro. Entrada: slug. Saída: o próprio slug; ManualNotFoundError se inválido."""
        if not SLUG_PATTERN.match(slug):
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        return slug

    def _visible_dirs(self, parent: Path) -> set[str]:
        """Lista pastas com nome de slug. Entrada: pasta pai. Saída: nomes."""
        return {entry.name for entry in parent.iterdir() if entry.is_dir() and SLUG_PATTERN.match(entry.name)}

    def _read_meta(self, slug: str) -> ManualMeta | None:
        """Lê o meta.json. Entrada: slug válido. Saída: ManualMeta ou None se não existir."""
        path = self._manuals_dir / slug / META_FILE
        if not path.is_file():
            return None
        data = json.loads(path.read_text("utf-8"))
        return ManualMeta(
            slug=data["slug"],
            published_at=datetime.fromisoformat(data["published_at"]),
            published_by=data["published_by"],
            images=tuple(data["images"]),
            warnings=tuple(data.get("warnings", [])),
        )

    def exists(self, slug: str) -> bool:
        """Diz se há manual (gerenciado ou legado). Entrada: slug. Saída: bool (False para slug inválido)."""
        if not SLUG_PATTERN.match(slug):
            return False
        return (self._assets_dir / slug).is_dir() or (self._manuals_dir / slug).is_dir()

    def is_legacy(self, slug: str) -> bool:
        """Diz se a pasta foi publicada fora da interface. Entrada: slug. Saída: bool."""
        return self.exists(slug) and self._read_meta(slug) is None

    def list_manuals(self) -> list[ManualSummary]:
        """Lista os manuais por slug. Entrada: nenhuma. Saída: resumos, legados incluídos."""
        summaries: list[ManualSummary] = []
        for slug in sorted(self._visible_dirs(self._assets_dir) | self._visible_dirs(self._manuals_dir)):
            meta = self._read_meta(slug)
            if meta is None:
                summaries.append(ManualSummary(slug, len(self.published_images(slug)), None, None, True))
            else:
                summaries.append(ManualSummary(slug, len(meta.images), meta.published_at, meta.published_by, False))
        return summaries

    def get_meta(self, slug: str) -> ManualMeta:
        """Lê os metadados. Entrada: slug. Saída: ManualMeta; ManualNotFoundError se não houver."""
        meta = self._read_meta(self._checked(slug))
        if meta is None:
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        return meta

    def get_markdown(self, slug: str) -> str:
        """Lê o .md original. Entrada: slug. Saída: texto; ManualNotFoundError se não houver (inclusive legado)."""
        path = self._manuals_dir / self._checked(slug) / f"{slug}.md"
        if not path.is_file():
            raise ManualNotFoundError(f"O manual “{slug}” não tem .md guardado nesta interface.")
        return path.read_text("utf-8")

    def published_images(self, slug: str) -> list[str]:
        """Lista as imagens publicadas. Entrada: slug. Saída: nomes ordenados (vazio se não houver pasta)."""
        folder = self._assets_dir / self._checked(slug)
        if not folder.is_dir():
            return []
        return sorted(entry.name for entry in folder.iterdir() if entry.is_file() and not entry.name.startswith("."))

    def diff(self, package: ManualPackage) -> ImageDiff:
        """Compara o envio com o publicado. Entrada: pacote. Saída: ImageDiff."""
        current = set(self.published_images(package.slug))
        new = set(package.images)
        folder = self._assets_dir / package.slug
        changed = sorted(
            name for name in current & new if _sha256(folder / name) != _sha256(package.images_dir / name)
        )
        return ImageDiff(tuple(sorted(new - current)), tuple(sorted(current - new)), tuple(changed))

    @contextmanager
    def _lock(self, slug: str) -> Iterator[None]:
        """Trava o manual durante a escrita. Entrada: slug. Saída: contexto; PublishInProgressError se ocupado."""
        with (self._locks_dir / f"{slug}.lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PublishInProgressError(
                    "Outra publicação ou exclusão deste manual está em andamento. Tente de novo em instantes."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def publish(self, package: ManualPackage, user_email: str) -> ManualMeta:
        """Publica imagens e .md trocando as pastas no final. Entrada: pacote e e-mail. Saída: ManualMeta gravado."""
        slug = self._checked(package.slug)
        with self._lock(slug):
            suffix = secrets.token_hex(6)
            images_tmp = self._assets_dir / f".{slug}.tmp-{suffix}"
            manual_tmp = self._manuals_dir / f".{slug}.tmp-{suffix}"
            try:
                self._stage_images(package, images_tmp)
            except FileNotFoundError as exc:
                # Os arquivos do envio sumiram (ex.: clique duplo em "Confirmar" já publicou e descartou o envio).
                shutil.rmtree(images_tmp, ignore_errors=True)
                raise PendingUploadError(PENDING_NOT_FOUND) from exc
            except BaseException:
                shutil.rmtree(images_tmp, ignore_errors=True)
                raise
            meta = ManualMeta(slug, self._clock(), user_email, package.images, package.warnings)
            manual_tmp.mkdir()
            (manual_tmp / f"{slug}.md").write_text(package.markdown, encoding="utf-8")
            meta_data = {
                "slug": meta.slug,
                "published_at": meta.published_at.isoformat(),
                "published_by": meta.published_by,
                "images": list(meta.images),
                "warnings": list(meta.warnings),
            }
            (manual_tmp / META_FILE).write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
            _swap_dir(self._assets_dir / slug, images_tmp, suffix)
            _swap_dir(self._manuals_dir / slug, manual_tmp, suffix)
        return meta

    def _stage_images(self, package: ManualPackage, images_tmp: Path) -> None:
        """Copia as imagens do envio para a pasta temporária. Entrada: pacote e pasta nova. Saída: arquivos gravados."""
        images_tmp.mkdir()
        for name in package.images:
            shutil.copyfile(package.images_dir / name, images_tmp / name)
            os.chmod(images_tmp / name, FILE_MODE)
        os.chmod(images_tmp, DIR_MODE)

    def delete(self, slug: str) -> None:
        """Apaga imagens e .md do manual. Entrada: slug. Saída: nenhuma; ManualNotFoundError se não existir."""
        self._checked(slug)
        if not self.exists(slug):
            raise ManualNotFoundError(f"Manual “{slug}” não encontrado.")
        with self._lock(slug):
            suffix = secrets.token_hex(6)
            _remove_dir(self._assets_dir / slug, suffix)
            _remove_dir(self._manuals_dir / slug, suffix)

    def new_staging(self) -> tuple[str, Path]:
        """Cria a pasta de trabalho de um envio. Entrada: nenhuma. Saída: (token, pasta)."""
        token = secrets.token_urlsafe(24)
        staging = self._pending_dir / token
        staging.mkdir()
        return token, staging

    def _staging_dir(self, token: str) -> Path:
        """Resolve a pasta do token. Entrada: token. Saída: caminho; PendingUploadError se o token for inválido."""
        if not TOKEN_PATTERN.match(token):
            raise PendingUploadError(PENDING_NOT_FOUND)
        return self._pending_dir / token

    def save_pending(self, token: str, package: ManualPackage, user_email: str) -> None:
        """Guarda um envio à espera de confirmação. Entrada: token, pacote extraído na pasta do token e e-mail. Saída: nenhuma."""
        staging = self._staging_dir(token)
        (staging / PENDING_MARKDOWN).write_text(package.markdown, encoding="utf-8")
        pending_data = {
            "slug": package.slug,
            "user": user_email,
            "created_at": self._clock().isoformat(),
            "images": list(package.images),
            "warnings": list(package.warnings),
        }
        (staging / PENDING_FILE).write_text(json.dumps(pending_data, ensure_ascii=False), encoding="utf-8")

    def load_pending(self, token: str, user_email: str) -> ManualPackage:
        """Recupera um envio pendente. Entrada: token e e-mail. Saída: ManualPackage; PendingUploadError se inválido ou expirado."""
        staging = self._staging_dir(token)
        try:
            data = json.loads((staging / PENDING_FILE).read_text("utf-8"))
            markdown = (staging / PENDING_MARKDOWN).read_text("utf-8")
        except (OSError, ValueError) as exc:
            raise PendingUploadError(PENDING_NOT_FOUND) from exc
        if data.get("user") != user_email:
            raise PendingUploadError(PENDING_NOT_FOUND)
        if self._clock() - datetime.fromisoformat(data["created_at"]) > PENDING_MAX_AGE:
            self.discard(token)
            raise PendingUploadError("O envio expirou (mais de 1 hora). Envie o zip de novo.")
        return ManualPackage(
            slug=data["slug"],
            markdown=markdown,
            images_dir=staging / "images",
            images=tuple(data["images"]),
            warnings=tuple(data["warnings"]),
        )

    def discard(self, token: str) -> None:
        """Apaga a pasta de um envio. Entrada: token. Saída: nenhuma."""
        shutil.rmtree(self._staging_dir(token), ignore_errors=True)
