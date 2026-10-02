"""Fábricas de zips e pacotes para os testes do kb_admin (nada aqui lê kb/)."""
from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n"
MANUAL_MD = "# Manual\n\n## Passo 1\n\nAbra o app.\n\n![Tela inicial](images/tela.png)\n"


def _write_zip(target: zipfile.ZipFile, files: Mapping[str, bytes | str], symlinks: Mapping[str, str]) -> None:
    """Grava arquivos e links simbólicos num zip aberto. Entrada: zip, arquivos e links. Saída: nenhuma."""
    for name, content in files.items():
        target.writestr(name, content)
    for name, link_target in symlinks.items():
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(info, link_target)


def build_zip(path: Path, files: Mapping[str, bytes | str], symlinks: Mapping[str, str] | None = None) -> Path:
    """Cria um .zip em disco. Entrada: caminho, {nome: conteúdo} e links opcionais. Saída: o caminho."""
    with zipfile.ZipFile(path, "w") as archive:
        _write_zip(archive, files, symlinks or {})
    return path


def zip_bytes(files: Mapping[str, bytes | str]) -> bytes:
    """Cria um .zip em memória. Entrada: {nome: conteúdo}. Saída: bytes do zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _write_zip(archive, files, {})
    return buffer.getvalue()


def manual_files(
    slug: str = "manual-teste", markdown: str | bytes = MANUAL_MD, images: tuple[str, ...] = ("tela.png",)
) -> dict[str, bytes | str]:
    """Monta o conteúdo de um pacote válido. Entrada: slug, .md e imagens. Saída: {caminho no zip: conteúdo}."""
    files: dict[str, bytes | str] = {f"{slug}/{slug}.md": markdown, f"{slug}/README.md": "Pacote RAG"}
    for name in images:
        files[f"{slug}/images/{name}"] = PNG + name.encode()
    return files
