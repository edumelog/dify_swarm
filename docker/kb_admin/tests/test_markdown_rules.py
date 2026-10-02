"""Testes das regras de imagem do .md (mesmas do publish.sh)."""
from kb_admin.markdown_rules import find_problems, referenced_images, rewrite_image_urls


def test_referenced_images_accepts_both_prefixes_and_deduplicates() -> None:
    """images/ e ./images/ contam; repetições aparecem uma vez, em ordem."""
    md = "![a](images/b.png)\n![c](./images/a.png)\n![d](images/b.png)\n"
    assert referenced_images(md) == ["a.png", "b.png"]


def test_valid_markdown_has_no_problems() -> None:
    """Referências corretas e existentes passam; URLs absolutas são mantidas."""
    md = "![Tela](images/tela.png)\n![Logo](https://x/logo.png)\n"
    assert find_problems(md, {"tela.png"}) == []


def test_html_image_is_rejected() -> None:
    """<img> não é suportado."""
    problems = find_problems('<img src="images/a.png">\n', {"a.png"})
    assert any("imagem em HTML" in p for p in problems)


def test_image_outside_images_dir_is_rejected() -> None:
    """Imagem fora de images/ (inclusive com colchete no texto alternativo) é recusada."""
    problems = find_problems("![a [b]](imgs/x.png)\n![c](../y.png)\n", set())
    assert sum("fora de images/" in p for p in problems) == 2


def test_reference_style_image_is_rejected() -> None:
    """Imagem estilo referência é recusada."""
    problems = find_problems("![a][id]\n\n[id]: images/a.png\n", {"a.png"})
    assert any("estilo referência" in p for p in problems)


def test_invalid_name_missing_file_and_bad_extension() -> None:
    """Nome com acento, arquivo ausente e extensão que não é imagem são todos listados."""
    md = "![a](images/ação.png)\n![b](images/falta.png)\n![c](images/doc.pdf)\n"
    problems = find_problems(md, {"doc.pdf"})
    assert any("nome inválido" in p and "ação.png" in p for p in problems)
    assert any("não encontrada" in p and "falta.png" in p for p in problems)
    assert any("formato de imagem não suportado" in p and "doc.pdf" in p for p in problems)


def test_extension_check_ignores_case() -> None:
    """Extensão em maiúsculas é aceita."""
    assert find_problems("![a](images/Tela.PNG)\n", {"Tela.PNG"}) == []


def test_rewrite_image_urls() -> None:
    """images/ e ./images/ viram a URL do manual; URLs absolutas ficam iguais."""
    md = "![a](images/a.png) ![b](./images/b.png) ![c](https://x/c.png)"
    result = rewrite_image_urls(md, "http://dify.dev.dti/kb-assets/manual")
    assert result == (
        "![a](http://dify.dev.dti/kb-assets/manual/a.png) "
        "![b](http://dify.dev.dti/kb-assets/manual/b.png) ![c](https://x/c.png)"
    )
