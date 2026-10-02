"""Simula como o Dify 1.17.1 processa um .md e recomenda os parâmetros de ingest (Parent-child)."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass

# --- Constantes copiadas da imagem langgenius/dify-api:1.17.1 em execução (conferir ao atualizar o Dify;
# o código-fonte em api/ deste repositório não é o mesmo da imagem) ---
# api/core/rag/extractor/markdown_extractor.py, markdown_to_tups()
DIFY_HEADER_PATTERN = re.compile(r"^#+\s")
DIFY_CODE_FENCE = "```"
DIFY_TAG_PATTERN = re.compile(r"<.*?>")
# api/core/rag/cleaner/clean_processor.py, clean(): limpeza padrão e "remove_extra_spaces"
DIFY_DEFAULT_CLEANING = (
    (re.compile(r"<\|"), "<"),
    (re.compile(r"\|>"), ">"),
    (re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"), ""),
    (re.compile("￾"), ""),
)
DIFY_EXTRA_NEWLINES = re.compile(r"\n{3,}")
DIFY_EXTRA_SPACES = re.compile(r"[\t\f\r\x20  ᠎ -   　]{2,}")

# --- Regras da recomendação (alinhadas ao prompt docker/kb_admin/seed/PDF_TO_RAG.md) ---
PARENT_DELIMITER = "\n\n\n"
PARAGRAPH_DELIMITER = "\n\n"
LINE_DELIMITER = "\n"
RECOMMENDED_SECTION_LENGTH = 3000
PARENT_LENGTH_STEP = 100
PARENT_MIN_LENGTH = 500
CHILD_LENGTH_STEP = 50
CHILD_MIN_LENGTH = 100
TOP_K_SECTIONS = 3
TOP_K_MIN = 3
TOP_K_MAX = 10
START_TITLE = "(início do documento)"
EXCERPT_LENGTH = 80
# O Dify lê o delimitador com unicode_escape: \N maiúsculo vira erro "malformed \N character escape".
DELIMITER_CASE_WARNING = " Digite \\n em minúsculas (use o botão Copiar): \\N maiúsculo quebra o processamento no Dify."


@dataclass(frozen=True)
class Section:
    """Seção do .md como o Dify a recebe: título (sem #) e texto já limpo."""

    title: str
    text: str


@dataclass(frozen=True)
class IngestRecommendation:
    """Parâmetros recomendados para o ingest no Dify e dados que os justificam."""

    parent_delimiter: str
    parent_max_length: int
    child_delimiter: str
    child_max_length: int
    child_length_cap: int
    top_k: int
    section_count: int
    largest_section_title: str
    largest_section_length: int
    longest_child_piece: int
    median_children: float
    estimated_child_chunks: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ParameterRow:
    """Linha da tabela de parâmetros: grupo e nome como na tela do Dify, valor e motivo."""

    group: str
    name: str
    value: str
    reason: str


def _split_by_headers(markdown: str) -> list[tuple[str | None, str]]:
    """Reproduz MarkdownExtractor.markdown_to_tups. Entrada: .md. Saída: pares (linha do título ou None, corpo)."""
    pairs: list[tuple[str | None, str]] = []
    header: str | None = None
    body = ""
    in_code = False
    for line in markdown.split("\n"):
        if line.startswith(DIFY_CODE_FENCE):
            in_code = not in_code
            body += line + "\n"
            continue
        if in_code:
            body += line + "\n"
            continue
        if DIFY_HEADER_PATTERN.match(line):
            pairs.append((header, body))
            header = line
            body = ""
        else:
            body += line + "\n"
    pairs.append((header, body))
    return pairs


def clean_text(text: str) -> str:
    """Aplica a limpeza padrão do Dify e 'Replace consecutive spaces'. Entrada: texto. Saída: texto limpo."""
    for pattern, replacement in DIFY_DEFAULT_CLEANING:
        text = pattern.sub(replacement, text)
    text = DIFY_EXTRA_NEWLINES.sub("\n\n", text)
    return DIFY_EXTRA_SPACES.sub(" ", text)


def extract_sections(markdown: str) -> list[Section]:
    """Divide o .md como o Dify faz antes do chunking. Entrada: .md. Saída: seções não vazias, na ordem."""
    sections: list[Section] = []
    for header, body in _split_by_headers(markdown):
        value = DIFY_TAG_PATTERN.sub("", body).strip()
        if header is None:
            title, content = START_TITLE, value
        else:
            title = header.replace("#", "").strip()
            content = f"\n\n{title}\n{value}"
        text = clean_text(content)
        if text.strip():
            sections.append(Section(title, text))
    return sections


def display_delimiter(delimiter: str) -> str:
    """Escapa quebras de linha como se digita no Dify. Entrada: delimitador. Saída: ex. '\\n\\n'."""
    return delimiter.replace("\n", "\\n")


def _round_up(value: int, step: int) -> int:
    """Arredonda para cima ao múltiplo. Entrada: valor e passo. Saída: múltiplo de step >= value."""
    return math.ceil(value / step) * step


def _format_int(value: int) -> str:
    """Formata inteiro no padrão brasileiro. Entrada: 3109. Saída: '3.109'."""
    return f"{value:,}".replace(",", ".")


def _excerpt(text: str) -> str:
    """Resume um trecho numa linha. Entrada: texto. Saída: até EXCERPT_LENGTH caracteres, com reticências."""
    flat = " ".join(text.split())
    return flat if len(flat) <= EXCERPT_LENGTH else flat[:EXCERPT_LENGTH] + "…"


def _pieces(text: str, delimiter: str) -> list[str]:
    """Corta como o splitter do Dify (sem juntar pedaços). Entrada: texto e delimitador. Saída: pedaços não vazios."""
    return [piece for piece in text.split(delimiter) if piece.strip()]


def _section_warnings(sections: list[Section], max_segmentation_length: int) -> list[str]:
    """Avisa sobre seções grandes. Entrada: seções e teto do Dify. Saída: mensagens."""
    warnings: list[str] = []
    for section in sections:
        size = len(section.text)
        if size > max_segmentation_length:
            warnings.append(
                f"A seção “{section.title}” tem {_format_int(size)} caracteres, acima do limite de "
                f"{_format_int(max_segmentation_length)} do Dify: ela será recortada e um passo pode ficar "
                "separado da sua imagem. Divida-a com subtítulos (PDF_TO_RAG, regra 3)."
            )
        elif size > RECOMMENDED_SECTION_LENGTH:
            warnings.append(
                f"A seção “{section.title}” tem {_format_int(size)} caracteres, acima dos "
                f"{_format_int(RECOMMENDED_SECTION_LENGTH)} recomendados (PDF_TO_RAG, regra 3)."
            )
    return warnings


def _markup_warnings(markdown: str) -> list[str]:
    """Avisa sobre texto que o Dify apaga (<...> no corpo e # no título). Entrada: .md. Saída: mensagens."""
    warnings: list[str] = []
    tags: list[str] = []
    for header, body in _split_by_headers(markdown):
        if header is not None:
            title = re.sub(r"^#+\s+", "", header).strip()
            if "#" in title:
                warnings.append(f"O Dify apaga o caractere # do título “{title}” (PDF_TO_RAG, regra 2).")
        tags.extend(
            match.group(0) for match in DIFY_TAG_PATTERN.finditer(body) if not match.group(0).startswith("<!--")
        )
    warnings.extend(
        f"O Dify apaga o texto entre < e >: {tag} (PDF_TO_RAG, regra 6)." for tag in dict.fromkeys(tags)
    )
    return warnings


def recommend(markdown: str, child_max_length: int, max_segmentation_length: int) -> IngestRecommendation:
    """Calcula os parâmetros de ingest. Entrada: .md com URLs absolutas, teto do filho e teto do Dify. Saída: recomendação."""
    sections = extract_sections(markdown)
    if not sections:
        raise ValueError("o .md não tem texto")
    largest = max(sections, key=lambda section: len(section.text))
    parent_max = min(
        max(_round_up(len(largest.text), PARENT_LENGTH_STEP), PARENT_MIN_LENGTH), max_segmentation_length
    )

    paragraphs = [_pieces(section.text, PARAGRAPH_DELIMITER) for section in sections]
    if max(len(piece) for pieces in paragraphs for piece in pieces) <= child_max_length:
        child_delimiter, children = PARAGRAPH_DELIMITER, paragraphs
    else:
        child_delimiter = LINE_DELIMITER
        children = [_pieces(section.text, LINE_DELIMITER) for section in sections]
    longest_child = max(len(piece) for pieces in children for piece in pieces)
    child_max = min(max(_round_up(longest_child, CHILD_LENGTH_STEP), CHILD_MIN_LENGTH), child_max_length)

    counts = [len(pieces) for pieces in children]
    median = float(statistics.median(counts))
    top_k = min(max(math.ceil(median * TOP_K_SECTIONS), TOP_K_MIN), TOP_K_MAX)

    warnings = _section_warnings(sections, max_segmentation_length)
    warnings.extend(
        f"Uma linha tem {_format_int(len(piece))} caracteres, acima do limite de {_format_int(child_max_length)} "
        "do chunk filho: o Dify vai cortá-la por espaço e grudar as palavras. Quebre a linha "
        f"(PDF_TO_RAG, regra 4): “{_excerpt(piece)}”"
        for pieces in children
        for piece in pieces
        if len(piece) > child_max_length
    )
    warnings.extend(_markup_warnings(markdown))
    return IngestRecommendation(
        parent_delimiter=PARENT_DELIMITER,
        parent_max_length=parent_max,
        child_delimiter=child_delimiter,
        child_max_length=child_max,
        child_length_cap=child_max_length,
        top_k=top_k,
        section_count=len(sections),
        largest_section_title=largest.title,
        largest_section_length=len(largest.text),
        longest_child_piece=longest_child,
        median_children=median,
        estimated_child_chunks=sum(counts),
        warnings=tuple(warnings),
    )


def parameter_rows(
    recommendation: IngestRecommendation, embedding_label: str, rerank_label: str
) -> list[ParameterRow]:
    """Monta a tabela da tela de ingest. Entrada: recomendação e nomes dos modelos. Saída: linhas na ordem do Dify."""
    rec = recommendation
    cap = _format_int(rec.child_length_cap)
    if rec.child_delimiter == PARAGRAPH_DELIMITER:
        child_reason = f"Todos os parágrafos cabem em {cap} caracteres: o filho é cada parágrafo."
    else:
        child_reason = f"Há parágrafos com mais de {cap} caracteres (ex.: tabelas): o filho é cada linha."
    median = f"{rec.median_children:g}".replace(".", ",")
    largest = f"Maior seção: “{rec.largest_section_title}”, com {_format_int(rec.largest_section_length)} caracteres."
    return [
        ParameterRow("Chunk Settings", "Modo", "Parent-child",
                     "O pai (a seção inteira, com as imagens) vai para o LLM; o filho, menor, é usado na busca."),
        ParameterRow("Parent-chunk", "Tipo", "Paragraph",
                     "Full-doc mandaria o manual inteiro como contexto e pularia a limpeza do texto."),
        ParameterRow("Parent-chunk", "Delimiter", display_delimiter(rec.parent_delimiter),
                     "Não aparece no texto limpo: cada seção vira um único pai, com o passo junto das suas imagens."
                     + DELIMITER_CASE_WARNING),
        ParameterRow("Parent-chunk", "Maximum chunk length", str(rec.parent_max_length), largest),
        ParameterRow("Child-chunk", "Delimiter", display_delimiter(rec.child_delimiter), child_reason + DELIMITER_CASE_WARNING),
        ParameterRow("Child-chunk", "Maximum chunk length", str(rec.child_max_length),
                     f"Maior pedaço: {_format_int(rec.longest_child_piece)} caracteres."),
        ParameterRow("Text Pre-processing Rules", "Replace consecutive spaces, newlines and tabs", "Marcado",
                     "Necessário para o delimitador do pai funcionar."),
        ParameterRow("Text Pre-processing Rules", "Delete all URLs and email addresses", "Desmarcado",
                     "Manuais citam e-mails e endereços de portais."),
        ParameterRow("Text Pre-processing Rules", "Summary Auto-Gen", "Desligado",
                     "O resumo não carrega as imagens."),
        ParameterRow("Index Method", "Index Method", "High Quality", "Exigido pelo modo Parent-child."),
        ParameterRow("Index Method", "Embedding Model", embedding_label,
                     "Modelo configurado na base; o app só exibe."),
        ParameterRow("Retrieval Setting", "Método", "Hybrid Search",
                     "Combina a busca semântica com a busca por palavra-chave."),
        ParameterRow("Retrieval Setting", "Rerank Model", rerank_label,
                     "Reordena os trechos encontrados. Sem rerank, use Weighted Score 0,7 semântico / "
                     "0,3 palavra-chave."),
        ParameterRow("Retrieval Setting", "Top K", str(rec.top_k),
                     f"Cerca de {TOP_K_SECTIONS} seções por pergunta (mediana de {median} filhos por seção). "
                     "Se a base tiver outros manuais, use o maior Top K recomendado entre eles."),
        ParameterRow("Retrieval Setting", "Score Threshold", "Desligado",
                     "Ajuste depois, testando no Dify (Retrieval Testing)."),
    ]
