"""Export filename convention (pure helper): `Client_Name_YYMMDD_vN[_kind].docx`,
slug sanitisation, and generic fallbacks. No DB, no network — the async
`resolve_export_filename` wrapper is covered by the export route tests."""

from __future__ import annotations

from datetime import date

from backend.services.export.filename import export_filename

_ON = date(2026, 6, 24)


def test_basic_format() -> None:
    name = export_filename(
        client_name="Acme", contract_name="Supply Agreement", version=3, on=_ON
    )
    assert name == "Acme_Supply Agreement_260624_v3.docx"


def test_kind_suffix_appended() -> None:
    name = export_filename(
        client_name="Acme",
        contract_name="Supply Agreement",
        version=1,
        on=_ON,
        kind="redline",
    )
    assert name == "Acme_Supply Agreement_260624_v1_redline.docx"


def test_slug_strips_illegal_characters() -> None:
    name = export_filename(
        client_name="Acme/Client: A*?", contract_name="NDA <2026>", version=2, on=_ON
    )
    assert name == "Acme Client A_NDA 2026_260624_v2.docx"


def test_empty_fields_fall_back_to_generic_labels() -> None:
    name = export_filename(client_name="", contract_name=None, version=1, on=_ON)
    assert name == "Client_Contract_260624_v1.docx"
