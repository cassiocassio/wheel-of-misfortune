"""Atomic JSON state store, serialised by one ``asyncio.Lock`` (SPEC §3, §6).

The whole app runs as a **single** Uvicorn worker, so one in-process lock around
every read-modify-write is sufficient and necessary: multiple workers would each
hold a divergent copy of ``state.json``. Writes go temp-file -> ``fsync`` ->
``os.replace`` so a reader never sees a torn file and a crash mid-write leaves the
previous good state intact.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


class StateStore:
    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def exists(self) -> bool:
        return self.path.exists()

    # ---- synchronous core (call inside the lock, or before the server starts) ----
    def load_sync(self) -> dict:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_sync(self, state: dict) -> None:
        """Atomic write: temp file in the same dir -> fsync -> ``os.replace``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=".state-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- async API used by the request handlers --------------------------------
    async def read(self) -> dict:
        async with self._lock:
            return self.load_sync()

    async def mutate(self, fn: Callable[[dict], T]) -> T:
        """Run ``fn(state)`` under the lock and persist the result atomically.

        ``fn`` mutates ``state`` in place and may return a value (handed back to
        the caller). The load, mutate and save happen with no ``await`` between
        them, so the operation is indivisible even before the lock — the lock
        guards against interleaving once handlers start awaiting elsewhere.
        """
        async with self._lock:
            state = self.load_sync()
            result = fn(state)
            self.save_sync(state)
            return result
