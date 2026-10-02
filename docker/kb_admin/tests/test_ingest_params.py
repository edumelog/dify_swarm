"""Testes da simulação do Dify 1.17.1 e da recomendação dos parâmetros de ingest."""
import pytest

from kb_admin.ingest_params import (
    START_TITLE,
    Section,
    display_delimiter,
    extract_sections,
    parameter_rows,
    recommend,
)

STEPS_MD = "## Passo 1\n\nAbra o app.\n\n![Tela](http://x/a.png)\n\n## Passo 2\n\nClique em OK.\n"
TABLE_LINES = "\n".join("|" + "c" * 278 + "|" for _ in range(8))
TABLE_MD = "# Manual\n\nTexto de apresentação.\n\n## Problemas Comuns\n\n" + TABLE_LINES + "\n"


def test_sections_follow_dify_extractor() -> None:
    """Cada título abre uma seção '\\n\\n<título>\\n<corpo>'; o texto antes do 1º título fica sem título."""
    sections = extract_sections("Introdução.\n## A\nTexto.\n")
    assert sections == [Section(START_TITLE, "Introdução."), Section("A", "\n\nA\nTexto.")]


def test_code_fence_lines_are_not_headers() -> None:
    """Linha com # dentro de bloco de código não abre seção."""
    sections = extract_sections("## Comando\n\n```\n# não é título\n```\n")
    assert len(sections) == 1
    assert "# não é título" in sections[0].text


def test_tags_are_removed_and_spaces_collapsed() -> None:
    """O Dify apaga <...> do corpo e a limpeza junta espaços repetidos."""
    sections = extract_sections("## Teclas\n\nPressione <Enter> para continuar.\n")
    assert sections[0].text == "\n\nTeclas\nPressione para continuar."


def test_hash_inside_title_is_removed() -> None:
    """Todo # do título é apagado."""
    assert extract_sections("## Dicas de C#\n\nTexto.\n")[0].title == "Dicas de C"


def test_empty_markdown_has_no_sections() -> None:
    """Sem texto não há seção, e recommend recusa."""
    assert extract_sections("\n\n") == []
    with pytest.raises(ValueError):
        recommend("", 1000, 4000)


def test_paragraph_mode_for_short_paragraphs() -> None:
    """Parágrafos curtos: filho por parágrafo; mínimos de 500 (pai) e 100 (filho)."""
    rec = recommend(STEPS_MD, 1000, 4000)
    assert rec.parent_delimiter == "\n\n\n"
    assert rec.parent_max_length == 500
    assert rec.largest_section_title == "Passo 1"
    assert rec.largest_section_length == 46
    assert rec.child_delimiter == "\n\n"
    assert rec.child_max_length == 100
    assert rec.longest_child_piece == 23
    assert rec.median_children == 1.5
    assert rec.top_k == 5
    assert rec.section_count == 2
    assert rec.estimated_child_chunks == 3
    assert rec.warnings == ()


def test_line_mode_like_office365_manual() -> None:
    """Tabela grande num parágrafo só: filho por linha, como no manual do Office 365."""
    rec = recommend(TABLE_MD, 1000, 4000)
    assert rec.parent_max_length == 2300
    assert rec.largest_section_length == 2266
    assert rec.child_delimiter == "\n"
    assert rec.child_max_length == 300
    assert rec.top_k == 10
    assert rec.warnings == ()


def test_section_warnings_and_parent_cap() -> None:
    """Seção acima de 3.000 gera aviso; acima do teto, erro e pai limitado ao teto."""
    md = "## Grande\n" + "a" * 3100 + "\n## Enorme\n" + "b" * 4100 + "\n"
    rec = recommend(md, 1000, 4000)
    assert rec.parent_max_length == 4000
    assert any("“Grande”" in w and "3.109" in w and "3.000" in w for w in rec.warnings)
    assert any("“Enorme”" in w and "4.109" in w and "4.000" in w for w in rec.warnings)


def test_line_above_child_cap_warns() -> None:
    """Linha maior que o teto do filho gera aviso e o tamanho fica no teto."""
    rec = recommend(STEPS_MD, 20, 4000)
    assert rec.child_delimiter == "\n"
    assert rec.child_max_length == 20
    assert any("23 caracteres" in w and "regra 4" in w for w in rec.warnings)


def test_tag_and_title_hash_warnings() -> None:
    """Avisos de <...> apagado e de # no título citam a regra do PDF_TO_RAG."""
    rec = recommend("## Dicas de C#\n\nPressione <Enter>.\n", 1000, 4000)
    assert any("<Enter>" in w and "regra 6" in w for w in rec.warnings)
    assert any("Dicas de C#" in w and "regra 2" in w for w in rec.warnings)


def test_display_delimiter() -> None:
    """Delimitadores aparecem escapados, como se digita no Dify."""
    assert display_delimiter("\n\n\n") == "\\n\\n\\n"


def test_parameter_rows() -> None:
    """A tabela mostra os valores calculados e os modelos configurados."""
    rows = parameter_rows(recommend(TABLE_MD, 1000, 4000), "text-embedding-3-small", "jina-reranker-v3")
    values = {(row.group, row.name): row.value for row in rows}
    assert values[("Parent-chunk", "Delimiter")] == "\\n\\n\\n"
    assert values[("Parent-chunk", "Maximum chunk length")] == "2300"
    assert values[("Child-chunk", "Delimiter")] == "\\n"
    assert values[("Child-chunk", "Maximum chunk length")] == "300"
    assert values[("Index Method", "Embedding Model")] == "text-embedding-3-small"
    assert values[("Retrieval Setting", "Rerank Model")] == "jina-reranker-v3"
    assert values[("Retrieval Setting", "Top K")] == "10"
    assert values[("Text Pre-processing Rules", "Delete all URLs and email addresses")] == "Desmarcado"


def test_html_comments_do_not_warn() -> None:
    """Comentários HTML (ex.: página de origem do PDF) somem no Dify sem prejuízo: não geram aviso."""
    rec = recommend("## A\n\n<!-- source_page: 3 -->\nTexto.\n", 1000, 4000)
    assert rec.warnings == ()


def test_cleaning_keeps_latin1_letters() -> None:
    """A limpeza do Dify 1.17.1 em execução não remove ï, ¿ nem ¾ (o código-fonte do repositório removia)."""
    assert extract_sections("## A\n\nÍndice ï ¿ ¾\n")[0].text == "\n\nA\nÍndice ï ¿ ¾"
