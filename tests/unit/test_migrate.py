"""Migration discovery + pending-diff — pure logic, no DB."""

from __future__ import annotations

from pathlib import Path

from backend.migrate import discover_migrations, pending_migrations


def test_discover_sorts_by_filename_and_reads_sql(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a migration", encoding="utf-8")

    found = discover_migrations(tmp_path)

    assert [version for version, _ in found] == ["0001_first", "0002_second"]
    assert found[0][1] == "SELECT 1;"


def test_discover_empty_dir_is_empty(tmp_path: Path) -> None:
    assert discover_migrations(tmp_path) == []


def test_pending_excludes_applied_and_preserves_order() -> None:
    available = [("0001_a", "A"), ("0002_b", "B"), ("0003_c", "C")]
    assert pending_migrations(available, {"0001_a"}) == [
        ("0002_b", "B"),
        ("0003_c", "C"),
    ]


def test_pending_empty_when_all_applied() -> None:
    available = [("0001_a", "A"), ("0002_b", "B")]
    assert pending_migrations(available, {"0001_a", "0002_b"}) == []
