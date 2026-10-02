"""Biblioteca de documentos de apoio (prompts, manuais de uso etc.) no volume privado."""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from kb_admin.errors import DocumentError, DocumentNotFoundError
from kb_admin.storage import utc_now

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_DESCRIPTION_LENGTH = 500
MAX_FILENAME_LENGTH = 200
COPY_CHUNK_BYTES = 1024 * 1024
DOC_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
CONTENT_FILE = "content"
META_FILE = "meta.json"
LOCK_FILE = ".lock"
SEED_MARKER = ".seeded"
SEED_DESCRIPTION = "Prompt de conversão PDF → Markdown (usar na LLM externa para gerar o .zip do manual)"
SEED_AUTHOR = "conteúdo inicial"
NOT_FOUND = "Documento não encontrado."


@dataclass(frozen=True)
class SupportDocument:
    """Documento de apoio: nome original, descrição, tamanho e última alteração."""

    id: str
    filename: str
    description: str
    size: int
    updated_at: datetime
    updated_by: str


def clean_filename(raw: str) -> str:
    """Valida o nome do arquivo enviado. Entrada: nome do navegador. Saída: nome sem caminho; DocumentError se inválido."""
    name = PurePosixPath(raw.replace("\\", "/")).name
    if not name or name in {".", ".."} or CONTROL_CHARS.search(name):
        raise DocumentError("Nome de arquivo inválido.")
    if len(name) > MAX_FILENAME_LENGTH:
        raise DocumentError(f"O nome do arquivo passa de {MAX_FILENAME_LENGTH} caracteres.")
    return name


def clean_description(raw: str) -> str:
    """Normaliza a descrição. Entrada: texto do formulário. Saída: texto numa linha; DocumentError se vazio ou longo."""
    text = " ".join(raw.split())
    if not text:
        raise DocumentError("Informe uma descrição.")
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise DocumentError(f"A descrição passa de {MAX_DESCRIPTION_LENGTH} caracteres.")
    return text


def _copy_limited(source: BinaryIO, target: Path) -> int:
    """Copia o upload com limite de tamanho. Entrada: origem e destino. Saída: bytes gravados; DocumentError se passar."""
    written = 0
    with target.open("wb") as output:
        while chunk := source.read(COPY_CHUNK_BYTES):
            written += len(chunk)
            if written > MAX_DOCUMENT_BYTES:
                raise DocumentError("O arquivo passa de 50 MB.")
            output.write(chunk)
    return written


class DocumentStore:
    """Guarda cada documento em <pasta>/<id>/ (arquivo 'content' + meta.json), com troca atômica de pastas."""

    def __init__(self, docs_dir: Path, clock: Callable[[], datetime] = utc_now) -> None:
        """Define a pasta da biblioteca. Entrada: pasta e relógio. Saída: nenhuma."""
        self._dir = docs_dir
        self._clock = clock

    def ensure_dirs(self) -> None:
        """Cria a pasta da biblioteca. Entrada: nenhuma. Saída: nenhuma."""
        self._dir.mkdir(parents=True, exist_ok=True)

    def cleanup_temporary(self) -> None:
        """Apaga sobras de operações interrompidas. Entrada: nenhuma. Saída: nenhuma."""
        for entry in self._dir.iterdir():
            if entry.is_dir() and entry.name.startswith(".") and (".tmp-" in entry.name or ".old-" in entry.name):
                shutil.rmtree(entry, ignore_errors=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Serializa as alterações da biblioteca. Entrada: nenhuma. Saída: contexto travado."""
        with (self._dir / LOCK_FILE).open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _folder(self, doc_id: str) -> Path:
        """Resolve a pasta do documento. Entrada: id. Saída: caminho; DocumentNotFoundError se o id for inválido."""
        if not DOC_ID_PATTERN.match(doc_id):
            raise DocumentNotFoundError(NOT_FOUND)
        return self._dir / doc_id

    def _read(self, folder: Path) -> SupportDocument | None:
        """Lê o meta.json de uma pasta. Entrada: pasta. Saída: SupportDocument ou None se não houver."""
        try:
            data = json.loads((folder / META_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return SupportDocument(
            id=data["id"],
            filename=data["filename"],
            description=data["description"],
            size=int(data["size"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            updated_by=data["updated_by"],
        )

    def _write_meta(self, folder: Path, document: SupportDocument) -> None:
        """Grava o meta.json de forma atômica. Entrada: pasta e documento. Saída: nenhuma."""
        data = {
            "id": document.id,
            "filename": document.filename,
            "description": document.description,
            "size": document.size,
            "updated_at": document.updated_at.isoformat(),
            "updated_by": document.updated_by,
        }
        temporary = folder / f".{META_FILE}.tmp"
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, folder / META_FILE)

    def list_documents(self) -> list[SupportDocument]:
        """Lista os documentos. Entrada: nenhuma. Saída: documentos em ordem alfabética do nome."""
        documents = [
            document
            for entry in self._dir.iterdir()
            if entry.is_dir() and DOC_ID_PATTERN.match(entry.name) and (document := self._read(entry)) is not None
        ]
        return sorted(documents, key=lambda document: document.filename.lower())

    def get(self, doc_id: str) -> SupportDocument:
        """Busca um documento. Entrada: id. Saída: SupportDocument; DocumentNotFoundError se não existir."""
        document = self._read(self._folder(doc_id))
        if document is None:
            raise DocumentNotFoundError(NOT_FOUND)
        return document

    def file_path(self, doc_id: str) -> Path:
        """Caminho do conteúdo no disco. Entrada: id. Saída: caminho do arquivo; DocumentNotFoundError se não existir."""
        self.get(doc_id)
        return self._folder(doc_id) / CONTENT_FILE

    def _check_unique(self, filename: str, ignore_id: str | None) -> None:
        """Recusa nome já usado por outro documento. Entrada: nome e id a ignorar. Saída: nenhuma; DocumentError se repetido."""
        for document in self.list_documents():
            if document.id != ignore_id and document.filename.lower() == filename.lower():
                raise DocumentError(f"Já existe um documento chamado “{document.filename}”. Use “Substituir” nele.")

    def _stage(self, source: BinaryIO) -> tuple[Path, int]:
        """Grava o upload numa pasta oculta. Entrada: arquivo enviado. Saída: (pasta, tamanho); apaga a pasta se falhar."""
        staging = self._dir / f".new.tmp-{secrets.token_hex(6)}"
        staging.mkdir()
        try:
            size = _copy_limited(source, staging / CONTENT_FILE)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return staging, size

    def create(self, source: BinaryIO, filename: str, description: str, user_email: str) -> SupportDocument:
        """Cria um documento. Entrada: arquivo, nome, descrição e e-mail. Saída: SupportDocument; DocumentError se inválido."""
        name = clean_filename(filename)
        text = clean_description(description)
        staging, size = self._stage(source)
        try:
            with self._lock():
                self._check_unique(name, None)
                document = SupportDocument(secrets.token_hex(8), name, text, size, self._clock(), user_email)
                self._write_meta(staging, document)
                staging.rename(self._dir / document.id)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return document

    def replace(self, doc_id: str, source: BinaryIO, filename: str, user_email: str) -> SupportDocument:
        """Troca o arquivo e mantém a descrição. Entrada: id, arquivo, nome e e-mail. Saída: SupportDocument atualizado."""
        self.get(doc_id)
        name = clean_filename(filename)
        staging, size = self._stage(source)
        try:
            with self._lock():
                current = self.get(doc_id)
                self._check_unique(name, doc_id)
                document = SupportDocument(doc_id, name, current.description, size, self._clock(), user_email)
                self._write_meta(staging, document)
                target = self._folder(doc_id)
                old = self._dir / f".{doc_id}.old-{secrets.token_hex(6)}"
                target.rename(old)
                try:
                    staging.rename(target)
                except BaseException:
                    old.rename(target)
                    raise
                # A troca já valeu; se a pasta antiga não sair agora, a limpeza da próxima subida a remove.
                shutil.rmtree(old, ignore_errors=True)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return document

    def update_description(self, doc_id: str, description: str, user_email: str) -> SupportDocument:
        """Edita a descrição. Entrada: id, descrição e e-mail. Saída: SupportDocument atualizado."""
        text = clean_description(description)
        with self._lock():
            current = self.get(doc_id)
            document = SupportDocument(doc_id, current.filename, text, current.size, self._clock(), user_email)
            self._write_meta(self._folder(doc_id), document)
        return document

    def delete(self, doc_id: str) -> None:
        """Apaga o documento. Entrada: id. Saída: nenhuma; DocumentNotFoundError se não existir."""
        with self._lock():
            self.get(doc_id)
            old = self._dir / f".{doc_id}.old-{secrets.token_hex(6)}"
            self._folder(doc_id).rename(old)
            shutil.rmtree(old, ignore_errors=True)

    def seed(self, seed_file: Path, description: str, author: str) -> bool:
        """Cria o conteúdo inicial uma única vez. Entrada: arquivo, descrição e autor. Saída: True se criou agora."""
        marker = self._dir / SEED_MARKER
        if marker.exists() or not seed_file.is_file():
            return False
        created = True
        with seed_file.open("rb") as source:
            try:
                self.create(source, seed_file.name, description, author)
            except DocumentError:
                # Já existe (subida anterior interrompida antes do marcador): só falta marcar.
                created = False
        marker.write_text("ok\n", encoding="utf-8")
        return created
