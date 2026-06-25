"""Export request models (F15b, DD-60).

The clean-copy export is a mutation (it cuts a snapshot), so its route is a POST
with a body. The recipient selector (DD-48) chooses which "shared with" pointer
the cut advances; `internal`/`copy_only` advance none.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ExportRecipient = Literal["counterparty", "legal", "internal", "copy_only"]


class CleanCopyExportRequest(BaseModel):
    recipient: ExportRecipient
