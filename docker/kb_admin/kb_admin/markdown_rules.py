"""Regras de imagem do .md (as mesmas do docker/kb_assets/publish.sh) e reescrita das URLs."""
from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import PurePosixPath

IMAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif", ".tif", ".tiff"}
)
# Texto alternativo com até um nível de colchetes, sem atravessar linhas (como o grep do publish.sh).
_ALT_TEXT = r"(?:[^\[\]\n]|\[[^\]\n]*\])*"
_HTML_IMAGE = re.compile(r"<img[^>\n]*>?", re.IGNORECASE)
_INLINE_IMAGE = re.compile(r"!\[" + _ALT_TEXT + r"\]\(([^)\n]*)\)")
_REFERENCE_IMAGE = re.compile(r"!\[" + _ALT_TEXT + r"\]\[[^\]\n]*\]")
_LOCAL_REFERENCE = re.compile(r"\]\((?:\./)?images/([^)\n]+)\)")
_LOCAL_TARGET = re.compile(r"^(?:\./)?images/")
_REMOTE_TARGET = re.compile(r"^https?://")
_REWRITE_TARGET = re.compile(r"\]\((?:\./)?images/")


def referenced_images(markdown: str) -> list[str]:
    """Lista as imagens citadas como images/ ou ./images/. Entrada: .md. Saída: nomes únicos ordenados."""
    return sorted(set(_LOCAL_REFERENCE.findall(markdown)))


def _syntax_problems(markdown: str) -> list[str]:
    """Acha imagens em formatos não suportados. Entrada: .md. Saída: mensagens de problema."""
    problems = [
        f"imagem em HTML não é suportada ({match.group(0)}); use ![descrição](images/arquivo.png)"
        for match in _HTML_IMAGE.finditer(markdown)
    ]
    for match in _INLINE_IMAGE.finditer(markdown):
        target = match.group(1)
        if not _LOCAL_TARGET.match(target) and not _REMOTE_TARGET.match(target):
            problems.append(
                f"referência de imagem fora de images/ não suportada ({match.group(0)}); "
                "mova o arquivo para images/ e use ![descrição](images/arquivo.png)"
            )
    problems.extend(
        f"imagem estilo referência não suportada ({match.group(0)}); "
        "use a forma inline ![descrição](images/arquivo.png)"
        for match in _REFERENCE_IMAGE.finditer(markdown)
    )
    return problems


def find_problems(markdown: str, available_images: Collection[str]) -> list[str]:
    """Valida as imagens do .md. Entrada: .md e nomes presentes em images/. Saída: todos os problemas (vazio se ok)."""
    problems = _syntax_problems(markdown)
    for name in referenced_images(markdown):
        if not IMAGE_NAME_PATTERN.match(name):
            problems.append(f"nome inválido (use só letras sem acento, números, '.', '_' e '-'): {name}")
        elif PurePosixPath(name).suffix.lower() not in IMAGE_EXTENSIONS:
            problems.append(
                f"formato de imagem não suportado: {name} (use png, jpg, gif, webp, svg, bmp, ico, avif ou tif)"
            )
        elif name not in available_images:
            problems.append(f"imagem referenciada não encontrada em images/: {name}")
    return problems


def rewrite_image_urls(markdown: str, slug_url: str) -> str:
    """Troca images/ e ./images/ pela URL pública. Entrada: .md e URL do manual sem barra final. Saída: .md novo."""
    return _REWRITE_TARGET.sub(lambda _match: f"]({slug_url}/", markdown)
