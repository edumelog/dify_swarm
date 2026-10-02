"""Testes da leitura e validação do pacote .zip."""
import io
from pathlib import Path

import pytest

from kb_admin.errors import PackageError
from kb_admin.package import ManualPackage, read_package, save_upload, slug_from_filename
from tests.helpers import MANUAL_MD, PNG, build_zip, manual_files


def _read(tmp_path: Path, filename: str, files: dict, symlinks: dict | None = None) -> ManualPackage:
    """Grava o zip e lê o pacote. Entrada: pasta, nome do zip, arquivos e links. Saída: ManualPackage."""
    zip_path = build_zip(tmp_path / "upload.zip", files, symlinks)
    return read_package(zip_path, filename, tmp_path / "staging")


def _problems(tmp_path: Path, filename: str, files: dict, symlinks: dict | None = None) -> list[str]:
    """Lê um pacote que deve falhar. Saída: lista de problemas do PackageError."""
    with pytest.raises(PackageError) as error:
        _read(tmp_path, filename, files, symlinks)
    return error.value.problems


def test_valid_package_with_root_folder(tmp_path: Path) -> None:
    """Pasta raiz com o nome do zip, como no pacote de exemplo."""
    package = _read(tmp_path, "manual-teste.zip", manual_files())
    assert package.slug == "manual-teste"
    assert package.markdown == MANUAL_MD
    assert package.images == ("tela.png",)
    assert (package.images_dir / "tela.png").read_bytes() == PNG + b"tela.png"
    assert package.warnings == ()


def test_valid_package_without_root_folder(tmp_path: Path) -> None:
    """Arquivos direto na raiz do zip também são aceitos."""
    files = {"manual-teste.md": MANUAL_MD, "images/tela.png": PNG}
    assert _read(tmp_path, "manual-teste.zip", files).images == ("tela.png",)


def test_only_referenced_images_are_extracted_and_extras_warned(tmp_path: Path) -> None:
    """Imagem não citada e arquivo estranho viram avisos e não são extraídos."""
    files = {**manual_files(images=("tela.png", "sobra.png")), "manual-teste/notas.txt": "x"}
    package = _read(tmp_path, "manual-teste.zip", files)
    assert not (package.images_dir / "sobra.png").exists()
    assert "imagem não citada no .md (não será publicada): sobra.png" in package.warnings
    assert "arquivo ignorado: notas.txt" in package.warnings


def test_mac_and_windows_artifacts_are_ignored(tmp_path: Path) -> None:
    """__MACOSX, .DS_Store e Zone.Identifier são ignorados sem aviso."""
    files = {
        **manual_files(),
        "__MACOSX/manual-teste/._x": "x",
        "manual-teste/.DS_Store": "x",
        "manual-teste/manual-teste.md:Zone.Identifier": "x",
    }
    assert _read(tmp_path, "manual-teste.zip", files).warnings == ()


def test_crlf_and_bom_are_normalized(tmp_path: Path) -> None:
    """.md do Windows (BOM + CRLF) é aceito e guardado com \\n."""
    md = "\ufeff" + MANUAL_MD.replace("\n", "\r\n")
    package = _read(tmp_path, "manual-teste.zip", manual_files(markdown=md.encode("utf-8")))
    assert package.markdown == MANUAL_MD


@pytest.mark.parametrize("filename", ["manual-x (1).zip", "Manual-X.zip", "manual_x.zip", "manual.rar", ".zip"])
def test_invalid_zip_names(filename: str) -> None:
    """Nome com espaço, maiúscula, sublinhado ou extensão errada é recusado."""
    with pytest.raises(PackageError):
        slug_from_filename(filename)


def test_slug_from_windows_path() -> None:
    """Navegadores antigos mandam o caminho completo; vale só o nome."""
    assert slug_from_filename("C:\\Users\\a\\manual-teste.zip") == "manual-teste"


def test_not_a_zip(tmp_path: Path) -> None:
    """Arquivo que não é zip gera mensagem clara."""
    fake = tmp_path / "upload.zip"
    fake.write_bytes(b"isto nao e um zip")
    with pytest.raises(PackageError) as error:
        read_package(fake, "manual-teste.zip", tmp_path / "staging")
    assert error.value.problems == ["o arquivo enviado não é um .zip válido"]


def test_markdown_must_match_zip_name(tmp_path: Path) -> None:
    """O .md precisa ter o mesmo nome do zip."""
    files = {"manual-teste/outro.md": MANUAL_MD, "manual-teste/images/tela.png": PNG}
    problems = _problems(tmp_path, "manual-teste.zip", files)
    assert problems == ["o zip deve conter o arquivo manual-teste.md (o mesmo nome do .zip); encontrado: outro.md"]


def test_root_folder_must_match_zip_name(tmp_path: Path) -> None:
    """Pasta raiz com outro nome é recusada."""
    problems = _problems(tmp_path, "manual-teste.zip", manual_files(slug="outro-nome"))
    assert "a pasta dentro do zip deve se chamar 'manual-teste'" in problems[0]


def test_unsafe_paths_and_symlinks(tmp_path: Path) -> None:
    """Zip slip e links simbólicos são recusados, todos listados."""
    files = {**manual_files(), "../fora.txt": "x", "/abs.txt": "x"}
    problems = _problems(
        tmp_path, "manual-teste.zip", files, symlinks={"manual-teste/images/link.png": "/etc/passwd"}
    )
    assert "caminho inseguro no zip: ../fora.txt" in problems
    assert "caminho inseguro no zip: /abs.txt" in problems
    assert "link simbólico não é permitido no zip: manual-teste/images/link.png" in problems


def test_markdown_rules_are_applied(tmp_path: Path) -> None:
    """Problemas de imagem do .md impedem a publicação."""
    md = "## A\n\n![x](images/falta.png)\n<img src='images/tela.png'>\n"
    problems = _problems(tmp_path, "manual-teste.zip", manual_files(markdown=md))
    assert any("não encontrada" in p for p in problems)
    assert any("imagem em HTML" in p for p in problems)


def test_empty_markdown_is_rejected(tmp_path: Path) -> None:
    """.md sem texto é recusado."""
    problems = _problems(tmp_path, "manual-teste.zip", manual_files(markdown="\n\n", images=()))
    assert problems == ["o manual-teste.md não tem texto"]


def test_non_utf8_markdown(tmp_path: Path) -> None:
    """.md fora de UTF-8 é recusado."""
    files = manual_files(markdown="## Ação\n".encode("latin-1"), images=())
    assert _problems(tmp_path, "manual-teste.zip", files) == ["o manual-teste.md precisa estar em UTF-8"]


def test_entry_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zip com entradas demais é recusado antes de extrair."""
    monkeypatch.setattr("kb_admin.package.MAX_ENTRIES", 2)
    problems = _problems(tmp_path, "manual-teste.zip", manual_files())
    assert problems == ["o zip tem 3 arquivos; o limite é 2"]


def test_uncompressed_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conteúdo descompactado acima do limite é recusado."""
    monkeypatch.setattr("kb_admin.package.MAX_UNCOMPRESSED_BYTES", 10)
    problems = _problems(tmp_path, "manual-teste.zip", manual_files())
    assert problems == ["o conteúdo descompactado passa de 200 MB"]


def test_save_upload_limit(tmp_path: Path) -> None:
    """Upload acima do limite é interrompido."""
    target = tmp_path / "upload.zip"
    save_upload(io.BytesIO(b"12345"), target, max_bytes=5)
    assert target.read_bytes() == b"12345"
    with pytest.raises(PackageError) as error:
        save_upload(io.BytesIO(b"123456"), target, max_bytes=5)
    assert error.value.problems == ["o .zip passa de 50 MB"]


def _patch_headers(data: bytes, local_offset: int, central_offset: int, value: int) -> bytes:
    """Grava um campo de 2 bytes nos cabeçalhos locais e no diretório central. Entrada: zip, posições e valor. Saída: zip alterado."""
    raw = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", local_offset), (b"PK\x01\x02", central_offset)):
        start = 0
        while (index := raw.find(signature, start)) != -1:
            raw[index + offset:index + offset + 2] = value.to_bytes(2, "little")
            start = index + 4
    return bytes(raw)


def _read_bytes(tmp_path: Path, data: bytes) -> list[str]:
    """Lê um zip dado em bytes que deve falhar. Entrada: pasta e bytes. Saída: problemas do PackageError."""
    zip_path = tmp_path / "upload.zip"
    zip_path.write_bytes(data)
    with pytest.raises(PackageError) as error:
        read_package(zip_path, "manual-teste.zip", tmp_path / "staging")
    return error.value.problems


def test_corrupted_deflate_data(tmp_path: Path) -> None:
    """Dados comprimidos corrompidos dão a mensagem de zip inválido, não erro 500."""
    import zipfile

    with zipfile.ZipFile(tmp_path / "ok.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manual-teste/manual-teste.md", MANUAL_MD * 50)
        archive.writestr("manual-teste/images/tela.png", PNG)
    raw = bytearray((tmp_path / "ok.zip").read_bytes())
    data_start = 30 + len("manual-teste/manual-teste.md")
    raw[data_start + 2:data_start + 60] = bytes([0xFF] * 58)
    assert _read_bytes(tmp_path, bytes(raw)) == ["o arquivo enviado não é um .zip válido"]


def test_password_protected_zip(tmp_path: Path) -> None:
    """Zip protegido por senha tem mensagem própria."""
    from tests.helpers import zip_bytes

    problems = _read_bytes(tmp_path, _patch_headers(zip_bytes(manual_files()), 6, 8, 0x1))
    assert problems == ["zip protegido por senha não é suportado; gere o .zip sem senha"]


def test_unsupported_compression_method(tmp_path: Path) -> None:
    """Método de compressão desconhecido dá a mensagem de zip inválido."""
    from tests.helpers import zip_bytes

    assert _read_bytes(tmp_path, _patch_headers(zip_bytes(manual_files()), 8, 10, 99)) == [
        "o arquivo enviado não é um .zip válido"
    ]
