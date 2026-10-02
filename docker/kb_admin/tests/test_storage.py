"""Testes do armazenamento dos manuais."""
import fcntl
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kb_admin.errors import ManualNotFoundError, PendingUploadError, PublishInProgressError
from kb_admin.package import ManualPackage
from kb_admin.storage import ManualStore
from tests.helpers import MANUAL_MD, PNG

START = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    """Relógio controlável para os testes."""

    def __init__(self) -> None:
        """Começa em START. Entrada: nenhuma. Saída: nenhuma."""
        self.now = START

    def __call__(self) -> datetime:
        """Devolve a hora atual simulada. Entrada: nenhuma. Saída: datetime UTC."""
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    """Relógio simulado. Saída: FakeClock."""
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> ManualStore:
    """Store com pastas temporárias já criadas. Saída: ManualStore."""
    manual_store = ManualStore(tmp_path / "assets", tmp_path / "data", clock=clock)
    manual_store.ensure_dirs()
    return manual_store


def make_package(base: Path, slug: str = "manual-teste", images: dict[str, bytes] | None = None) -> ManualPackage:
    """Cria um pacote já extraído. Entrada: pasta, slug e {imagem: bytes}. Saída: ManualPackage."""
    files = {"tela.png": PNG} if images is None else images
    images_dir = base / f"pkg-{slug}-{len(list(base.glob('pkg-*')))}" / "images"
    images_dir.mkdir(parents=True)
    for name, content in files.items():
        (images_dir / name).write_bytes(content)
    return ManualPackage(slug, MANUAL_MD, images_dir, tuple(sorted(files)), ())


def pending_package(staging: Path) -> ManualPackage:
    """Cria um pacote extraído dentro da pasta do envio. Entrada: pasta do token. Saída: ManualPackage."""
    (staging / "images").mkdir()
    (staging / "images/tela.png").write_bytes(PNG)
    return ManualPackage("manual-teste", MANUAL_MD, staging / "images", ("tela.png",), ("aviso",))


def test_publish_writes_images_markdown_and_meta(store: ManualStore, tmp_path: Path) -> None:
    """Publicar grava as imagens no volume público e o .md e o meta no privado."""
    meta = store.publish(make_package(tmp_path), "ana@camara.rj")
    assert (tmp_path / "assets/manual-teste/tela.png").read_bytes() == PNG
    assert (tmp_path / "assets/manual-teste/tela.png").stat().st_mode & 0o777 == 0o644
    assert store.get_markdown("manual-teste") == MANUAL_MD
    assert store.get_meta("manual-teste") == meta
    assert meta.published_by == "ana@camara.rj" and meta.published_at == START
    assert not (tmp_path / "assets/manual-teste/manual-teste.md").exists()


def test_republish_replaces_everything_without_leftovers(store: ManualStore, tmp_path: Path) -> None:
    """Republicar troca as imagens e não deixa pastas temporárias."""
    store.publish(make_package(tmp_path, images={"a.png": PNG, "b.png": PNG}), "ana@camara.rj")
    store.publish(make_package(tmp_path, images={"b.png": PNG + b"novo", "c.png": PNG}), "bia@camara.rj")
    assert store.published_images("manual-teste") == ["b.png", "c.png"]
    assert [p.name for p in (tmp_path / "assets").iterdir()] == ["manual-teste"]
    assert [p.name for p in (tmp_path / "data/manuals").iterdir()] == ["manual-teste"]


def test_diff(store: ManualStore, tmp_path: Path) -> None:
    """O diff separa imagens novas, removidas e alteradas."""
    store.publish(make_package(tmp_path, images={"a.png": PNG, "b.png": PNG, "c.png": PNG}), "ana@camara.rj")
    diff = store.diff(make_package(tmp_path, images={"b.png": PNG, "c.png": PNG + b"x", "d.png": PNG}))
    assert (diff.added, diff.removed, diff.changed) == (("d.png",), ("a.png",), ("c.png",))
    assert diff.has_changes


def test_list_includes_legacy_folders(store: ManualStore, tmp_path: Path) -> None:
    """Pasta publicada pelo publish.sh aparece como legada; pastas ocultas não aparecem."""
    store.publish(make_package(tmp_path), "ana@camara.rj")
    (tmp_path / "assets/office365").mkdir()
    (tmp_path / "assets/office365/x.png").write_bytes(PNG)
    (tmp_path / "assets/.office365.tmp").mkdir()
    summaries = store.list_manuals()
    assert [(s.slug, s.legacy, s.image_count) for s in summaries] == [
        ("manual-teste", False, 1),
        ("office365", True, 1),
    ]
    assert store.is_legacy("office365") and not store.is_legacy("manual-teste")
    with pytest.raises(ManualNotFoundError):
        store.get_markdown("office365")


def test_delete_removes_both_sides(store: ManualStore, tmp_path: Path) -> None:
    """Apagar remove imagens e .md; apagar de novo dá erro."""
    store.publish(make_package(tmp_path), "ana@camara.rj")
    store.delete("manual-teste")
    assert not store.exists("manual-teste")
    assert list((tmp_path / "assets").iterdir()) == []
    with pytest.raises(ManualNotFoundError):
        store.delete("manual-teste")


@pytest.mark.parametrize("slug", ["../data", "Manual", ".hidden", ""])
def test_invalid_slugs_are_not_found(store: ManualStore, slug: str) -> None:
    """Slug inválido nunca vira caminho no disco."""
    assert not store.exists(slug)
    with pytest.raises(ManualNotFoundError):
        store.delete(slug)


def test_lock_blocks_concurrent_publish(store: ManualStore, tmp_path: Path) -> None:
    """Com o lock do manual ocupado, publicar falha sem mexer em nada."""
    with (tmp_path / "data/locks/manual-teste.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with pytest.raises(PublishInProgressError):
            store.publish(make_package(tmp_path), "ana@camara.rj")
    assert not store.exists("manual-teste")


def test_pending_roundtrip(store: ManualStore) -> None:
    """Pendente salvo pode ser lido pelo mesmo usuário e descartado."""
    token, staging = store.new_staging()
    pending = pending_package(staging)
    store.save_pending(token, pending, "ana@camara.rj")
    assert store.load_pending(token, "ana@camara.rj") == pending
    with pytest.raises(PendingUploadError):
        store.load_pending(token, "outra@camara.rj")
    store.discard(token)
    with pytest.raises(PendingUploadError):
        store.load_pending(token, "ana@camara.rj")


def test_pending_expires_after_one_hour(store: ManualStore, clock: FakeClock) -> None:
    """Pendente com mais de 1 hora expira e some."""
    token, staging = store.new_staging()
    store.save_pending(token, pending_package(staging), "ana@camara.rj")
    clock.now = START + timedelta(hours=1, minutes=1)
    with pytest.raises(PendingUploadError, match="expirou"):
        store.load_pending(token, "ana@camara.rj")
    assert not staging.exists()


def test_cleanup_removes_leftovers(store: ManualStore, tmp_path: Path, clock: FakeClock) -> None:
    """A limpeza da subida remove temporários e pendentes velhos."""
    (tmp_path / "assets/.manual-teste.tmp-abc").mkdir()
    (tmp_path / "data/manuals/.manual-teste.old-abc").mkdir()
    token, staging = store.new_staging()
    store.save_pending(token, pending_package(staging), "ana@camara.rj")
    clock.now = START + timedelta(hours=2)
    store.cleanup_temporary()
    assert list((tmp_path / "assets").iterdir()) == []
    assert list((tmp_path / "data/manuals").iterdir()) == []
    assert not staging.exists()


def test_invalid_token(store: ManualStore) -> None:
    """Token com caracteres estranhos é recusado."""
    with pytest.raises(PendingUploadError):
        store.load_pending("../../etc", "ana@camara.rj")


def test_meta_file_format(store: ManualStore, tmp_path: Path) -> None:
    """meta.json guarda os campos da spec."""
    store.publish(make_package(tmp_path), "ana@camara.rj")
    data = json.loads((tmp_path / "data/manuals/manual-teste/meta.json").read_text())
    assert data == {
        "slug": "manual-teste",
        "published_at": "2026-10-02T12:00:00+00:00",
        "published_by": "ana@camara.rj",
        "images": ["tela.png"],
        "warnings": [],
    }


def test_publish_with_vanished_files_cleans_up(store: ManualStore, tmp_path: Path) -> None:
    """Se os arquivos do envio sumiram (clique duplo em confirmar), publicar dá 'envio não encontrado' sem deixar sobras."""
    package = make_package(tmp_path)
    (package.images_dir / "tela.png").unlink()
    with pytest.raises(PendingUploadError):
        store.publish(package, "ana@camara.rj")
    assert list((tmp_path / "assets").iterdir()) == []
    assert list((tmp_path / "data/manuals").iterdir()) == []
