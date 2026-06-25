"""Export request models (F15b, DD-43, DD-71).

DD-71 decoupled export from "send": a clean-copy export is now a pure grab — it
renders the current DB state to a .docx and streams it, cutting no snapshot and
advancing no pointer. So the export route takes **no request body** (the prior
`recipient` selector + `CleanCopyExportRequest` are gone — the snapshot-cut +
pointer-advance moved to the separate Mark-as-sent action, `models/mark_sent.py`).
This module is intentionally empty of request shapes; it stays as the documented
home for the export contract.
"""

from __future__ import annotations
