"""Testes da biblioteca de documentos de apoio."""
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kb_admin.docs_store import DocumentStore
from kb_admin.errors import DocumentError, DocumentNotFoundError

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def docs(tmp_path: Path) -> DocumentStore:
    """Biblioteca em pasta temporária. Saída: DocumentStore."""
    store = DocumentStore(tmp_path / "docs", clock=lambda: NOW)
    store.ensure_dirs()
    return store


def add(docs: DocumentStore, name: str = "prompt.md", content: bytes = b"conteudo", desc: str = "Prompt") -> str:
    """Cria um documento. Entrada: biblioteca, nome, conteúdo e descrição. Saída: id."""
    return docs.create(io.BytesIO(content), name, desc, "ana@camara.rj").id


def test_create_and_read(docs: DocumentStore) -> None:
    """Criar grava conteúdo e metadados."""
    doc_id = add(docs, "Instruções.pdf", b"%PDF", "  Manual   de\nuso  ")
    doc = docs.get(doc_id)
    assert (doc.filename, doc.description, doc.size, doc.updated_by) == ("Instruções.pdf", "Manual de uso", 4, "ana@camara.rj")
    assert doc.updated_at == NOW
    assert docs.file_path(doc_id).read_bytes() == b"%PDF"
    assert [d.id for d in docs.list_documents()] == [doc_id]


def test_list_is_alphabetical(docs: DocumentStore) -> None:
    """A lista sai em ordem alfabética, sem diferenciar maiúsculas."""
    add(docs, "b.md")
    add(docs, "A.md")
    assert [d.filename for d in docs.list_documents()] == ["A.md", "b.md"]


def test_duplicate_name_is_refused(docs: DocumentStore) -> None:
    """Nome repetido (sem diferenciar maiúsculas) é recusado."""
    add(docs, "Prompt.md")
    with pytest.raises(DocumentError, match="Substituir"):
        add(docs, "prompt.md")


@pytest.mark.parametrize("name", ["", ".", "..", "a\x00b.md", "x" * 201 + ".md"])
def test_invalid_names(docs: DocumentStore, name: str) -> None:
    """Nomes vazios, especiais, com controle ou longos demais são recusados."""
    with pytest.raises(DocumentError):
        add(docs, name)


def test_path_is_stripped_and_meta_json_is_safe(docs: DocumentStore, tmp_path: Path) -> None:
    """Caminho no nome é descartado; um arquivo chamado meta.json não corrompe os metadados."""
    doc_id = add(docs, "..\\..\\meta.json", b'{"x": 1}')
    assert docs.get(doc_id).filename == "meta.json"
    assert docs.file_path(doc_id).read_bytes() == b'{"x": 1}'
    assert not (tmp_path / "meta.json").exists()


def test_description_rules(docs: DocumentStore) -> None:
    """Descrição vazia ou longa demais é recusada."""
    with pytest.raises(DocumentError, match="descrição"):
        add(docs, desc="   ")
    with pytest.raises(DocumentError, match="500"):
        add(docs, desc="x" * 501)


def test_size_limit_leaves_nothing(docs: DocumentStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Arquivo acima do limite é recusado sem deixar pasta temporária."""
    monkeypatch.setattr("kb_admin.docs_store.MAX_DOCUMENT_BYTES", 3)
    with pytest.raises(DocumentError, match="50 MB"):
        add(docs, content=b"1234")
    assert [p.name for p in (tmp_path / "docs").iterdir() if not p.name.startswith(".lock")] == []


def test_replace_keeps_description_and_can_rename(docs: DocumentStore) -> None:
    """Substituir troca o arquivo e o nome e mantém a descrição."""
    doc_id = add(docs, "v1.md", b"um", "Prompt oficial")
    doc = docs.replace(doc_id, io.BytesIO(b"dois!"), "v2.md", "bia@camara.rj")
    assert (doc.filename, doc.description, doc.size, doc.updated_by) == ("v2.md", "Prompt oficial", 5, "bia@camara.rj")
    assert docs.file_path(doc_id).read_bytes() == b"dois!"


def test_replace_refuses_name_of_other_document(docs: DocumentStore) -> None:
    """Substituir não pode assumir o nome de outro documento."""
    add(docs, "a.md")
    doc_id = add(docs, "b.md")
    with pytest.raises(DocumentError):
        docs.replace(doc_id, io.BytesIO(b"x"), "A.md", "ana@camara.rj")
    assert docs.get(doc_id).filename == "b.md"


def test_update_description(docs: DocumentStore) -> None:
    """Editar a descrição mantém o arquivo."""
    doc_id = add(docs)
    assert docs.update_description(doc_id, "Nova descrição", "bia@camara.rj").description == "Nova descrição"
    assert docs.file_path(doc_id).read_bytes() == b"conteudo"


def test_delete_and_not_found(docs: DocumentStore) -> None:
    """Apagar remove o documento; ids inválidos ou inexistentes dão 'não encontrado'."""
    doc_id = add(docs)
    docs.delete(doc_id)
    assert docs.list_documents() == []
    for bad in (doc_id, "../etc", "ABCDEF0123456789"):
        with pytest.raises(DocumentNotFoundError):
            docs.get(bad)


def test_seed_runs_once_and_does_not_come_back(docs: DocumentStore, tmp_path: Path) -> None:
    """O conteúdo inicial entra uma vez; apagado, não volta."""
    seed = tmp_path / "PDF_TO_RAG.md"
    seed.write_text("# Prompt\n", encoding="utf-8")
    assert docs.seed(seed, "Prompt de conversão", "conteúdo inicial") is True
    [doc] = docs.list_documents()
    assert (doc.filename, doc.updated_by) == ("PDF_TO_RAG.md", "conteúdo inicial")
    docs.delete(doc.id)
    assert docs.seed(seed, "Prompt de conversão", "conteúdo inicial") is False
    assert docs.list_documents() == []


def test_seed_missing_file_waits(docs: DocumentStore, tmp_path: Path) -> None:
    """Sem o arquivo de seed, nada é marcado e ele entra quando existir."""
    seed = tmp_path / "PDF_TO_RAG.md"
    assert docs.seed(seed, "d", "a") is False
    seed.write_text("x", encoding="utf-8")
    assert docs.seed(seed, "d", "a") is True


def test_cleanup_removes_leftovers(docs: DocumentStore, tmp_path: Path) -> None:
    """Sobras temporárias somem na limpeza."""
    (tmp_path / "docs/.new.tmp-abc").mkdir()
    (tmp_path / "docs/.0123456789abcdef.old-abc").mkdir()
    docs.cleanup_temporary()
    assert sorted(p.name for p in (tmp_path / "docs").iterdir() if p.is_dir()) == []
