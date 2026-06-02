"""Request bodies for the API (SPEC §6). Responses are plain dicts built in
``main.py`` from state + engine output."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

# Identifiers are short human/slug strings; bound them so an empty or absurdly
# long value can't slip past validation into the state file or the spin cache.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Token = Annotated[str, StringConstraints(max_length=128)]


class SpinRequest(BaseModel):
    player: Name
    # short-lived idempotency token so a double-tap doesn't double-assign (§6)
    spin_token: Token | None = None


class TaskAction(BaseModel):
    """Body shared by reroll / accept / done / daily — a player acting on a task."""

    player: Name
    task_id: Name
