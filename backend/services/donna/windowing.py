"""Pure windowing for the Donna conversation (DD-40): the last N turns plus a rolling
summary. No I/O — it operates on already-loaded messages, so it is unit-testable and
reused by the eval. The summarisation LLM call lives in the service (qa.py); this module
only pairs messages into turns, selects the window, and identifies the single turn that
just fell out of it (folded into the rolling summary)."""

from __future__ import annotations

from backend.models.donna import DonnaMessage, DonnaTurn

# DD-40 window size. A conversation-shaping policy constant, not a model/token limit
# (so not a DD-35 config value): the number of recent turns injected verbatim.
WINDOW_TURNS = 10


def to_turns(messages: list[DonnaMessage]) -> list[DonnaTurn]:
    """Pair consecutive (user, assistant) messages into turns. A trailing user message
    with no assistant reply yet is ignored (it is not a complete turn)."""
    turns: list[DonnaTurn] = []
    pending_question: str | None = None
    for message in messages:
        if message.role == "user":
            pending_question = message.content
        elif pending_question is not None:
            turns.append(DonnaTurn(question=pending_question, answer=message.content))
            pending_question = None
    return turns


def window(turns: list[DonnaTurn]) -> list[DonnaTurn]:
    """The last WINDOW_TURNS turns — the only history injected verbatim."""
    return turns[-WINDOW_TURNS:]


def evicted_turn(turns: list[DonnaTurn]) -> DonnaTurn | None:
    """The single turn that left the window when the most recent turn was appended, or
    None while the thread is still within the window. Once past the window each new turn
    evicts exactly one — the turn WINDOW_TURNS+1 back — so summary updates stay O(1)."""
    if len(turns) <= WINDOW_TURNS:
        return None
    return turns[len(turns) - WINDOW_TURNS - 1]


def render_history(turns: list[DonnaTurn]) -> str:
    return "\n".join(f"You: {turn.question}\nDonna: {turn.answer}" for turn in turns)
