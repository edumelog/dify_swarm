"""Leitura segura e validação do pacote .zip de um manual."""
from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from kb_admin import markdown_rules
from kb_admin.errors import PackageError
from kb_admin.ingest_params import extract_sections

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 500
COPY_CHUNK_BYTES = 1024 * 1024
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
UPLOADED_ZIP = "upload.zip"
IMAGES_DIR = "images"
README_NAME = "readme.md"
IGNORED_PREFIXES = ("__MACOSX/",)
IGNORED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
TOO_BIG = "o conteúdo descompactado passa de 200 MB"


@dataclass(frozen=True)
class ManualPackage:
    """Pacote validado: slug, .md original e imagens citadas já extraídas em images_dir."""

    slug: str
    markdown: str
    images_dir: Path
    images: tuple[str, ...]
    warnings: tuple[str, ...]


def slug_from_filename(filename: str) -> str:
    """Tira o slug do nome do zip. Entrada: nome enviado pelo navegador. Saída: slug; PackageError se inválido."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name.lower().endswith(".zip"):
        raise PackageError([f"envie um arquivo .zip (recebido: {name or 'sem nome'})"])
    slug = name[: -len(".zip")]
    if not SLUG_PATTERN.match(slug):
        raise PackageError([
            f"nome de arquivo inválido '{name}': use só letras minúsculas sem acento, números e hífen, "
            "como manual-office365-rag.zip"
        ])
    return slug


def save_upload(source: BinaryIO, target: Path, max_bytes: int = MAX_ZIP_BYTES) -> None:
    """Grava o upload em disco com limite. Entrada: arquivo enviado, destino e limite. Saída: nenhuma; PackageError se passar."""
    written = 0
    with target.open("wb") as output:
        while chunk := source.read(COPY_CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                raise PackageError(["o .zip passa de 50 MB"])
            output.write(chunk)


def _is_ignored(name: str) -> bool:
    """Diz se a entrada é lixo de sistema (macOS/Windows). Entrada: caminho no zip. Saída: True se ignorar."""
    return (
        name.startswith(IGNORED_PREFIXES)
        or PurePosixPath(name).name in IGNORED_NAMES
        or name.endswith(":Zone.Identifier")
    )


def _is_unsafe(name: str) -> bool:
    """Diz se o caminho escaparia da pasta de destino. Entrada: caminho no zip. Saída: True se inseguro."""
    return (
        "\\" in name
        or name.startswith("/")
        or bool(WINDOWS_DRIVE.match(name))
        or ".." in PurePosixPath(name).parts
    )


def _safe_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Confere limites e caminhos. Entrada: zip aberto. Saída: {caminho: ZipInfo} só de arquivos; PackageError se houver problema."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PackageError([f"o zip tem {len(infos)} arquivos; o limite é {MAX_ENTRIES}"])
    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
        raise PackageError([TOO_BIG])
    problems: list[str] = []
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if _is_ignored(name):
            continue
        if _is_unsafe(name):
            problems.append(f"caminho inseguro no zip: {name}")
        elif stat.S_ISLNK(info.external_attr >> 16):
            problems.append(f"link simbólico não é permitido no zip: {name}")
        elif info.is_dir():
            continue
        elif name in entries:
            problems.append(f"arquivo repetido no zip: {name}")
        else:
            entries[name] = info
    if problems:
        raise PackageError(problems)
    return entries


def _strip_root(entries: dict[str, zipfile.ZipInfo], slug: str) -> dict[str, zipfile.ZipInfo]:
    """Remove a pasta raiz <slug>/ se houver. Entrada: entradas e slug. Saída: entradas relativas ao pacote."""
    prefix = f"{slug}/"
    if entries and all(name.startswith(prefix) for name in entries):
        return {name[len(prefix):]: info for name, info in entries.items()}
    top_dirs = {name.split("/", 1)[0] for name in entries if "/" in name}
    root_files = [name for name in entries if "/" not in name]
    if not root_files and len(top_dirs) == 1 and top_dirs != {IMAGES_DIR}:
        raise PackageError([
            f"a pasta dentro do zip deve se chamar '{slug}' (o mesmo nome do .zip); "
            f"encontrada: '{next(iter(top_dirs))}'"
        ])
    return entries


def _read_markdown(archive: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo], slug: str) -> str:
    """Lê o <slug>.md. Entrada: zip, entradas e slug. Saída: texto com \\n; PackageError se faltar ou não for UTF-8."""
    expected = f"{slug}.md"
    info = entries.get(expected)
    if info is None:
        others = sorted(
            name
            for name in entries
            if "/" not in name and name.lower().endswith(".md") and name.lower() != README_NAME
        )
        found = f"; encontrado: {', '.join(others)}" if others else ""
        raise PackageError([f"o zip deve conter o arquivo {expected} (o mesmo nome do .zip){found}"])
    try:
        text = archive.read(info).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PackageError([f"o {expected} precisa estar em UTF-8"]) from exc
    return text.replace("\r\n", "\n")


def _is_image_entry(name: str) -> bool:
    """Diz se a entrada fica direto em images/. Entrada: caminho relativo. Saída: True para images/<arquivo>."""
    parts = name.split("/")
    return len(parts) == 2 and parts[0] == IMAGES_DIR


def _extract_images(
    archive: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo], images: tuple[str, ...], target_dir: Path
) -> None:
    """Extrai só as imagens citadas, contando os bytes. Entrada: zip, entradas, nomes e destino. Saída: arquivos gravados."""
    written = 0
    for name in images:
        with archive.open(entries[f"{IMAGES_DIR}/{name}"]) as source, (target_dir / name).open("wb") as target:
            while chunk := source.read(COPY_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UNCOMPRESSED_BYTES:
                    raise PackageError([TOO_BIG])
                target.write(chunk)


def _warnings(entries: dict[str, zipfile.ZipInfo], slug: str, images: tuple[str, ...]) -> list[str]:
    """Lista o que será ignorado. Entrada: entradas, slug e imagens citadas. Saída: avisos."""
    referenced = set(images)
    warnings: list[str] = []
    for name in sorted(entries):
        if name == f"{slug}.md" or name.lower() == README_NAME:
            continue
        if _is_image_entry(name):
            image = name.removeprefix(f"{IMAGES_DIR}/")
            if image not in referenced:
                warnings.append(f"imagem não citada no .md (não será publicada): {image}")
        else:
            warnings.append(f"arquivo ignorado: {name}")
    return warnings


def read_package(zip_path: Path, filename: str, staging_dir: Path) -> ManualPackage:
    """Valida o zip e extrai as imagens citadas. Entrada: zip salvo, nome original e pasta de trabalho. Saída: ManualPackage."""
    slug = slug_from_filename(filename)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = _strip_root(_safe_entries(archive), slug)
            markdown = _read_markdown(archive, entries, slug)
            available = {name.removeprefix(f"{IMAGES_DIR}/") for name in entries if _is_image_entry(name)}
            problems = markdown_rules.find_problems(markdown, available)
            if not extract_sections(markdown):
                problems.append(f"o {slug}.md não tem texto")
            if problems:
                raise PackageError(problems)
            images = tuple(markdown_rules.referenced_images(markdown))
            images_dir = staging_dir / IMAGES_DIR
            images_dir.mkdir(parents=True, exist_ok=True)
            _extract_images(archive, entries, images, images_dir)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise PackageError(["o arquivo enviado não é um .zip válido"]) from exc
    return ManualPackage(slug, markdown, images_dir, images, tuple(_warnings(entries, slug, images)))
