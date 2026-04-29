"""Per-bundle custom instructions for the NimbleBrain platform.

NimbleBrain reads `app://instructions` from every active bundle on each
prompt assembly and wraps any non-empty body in `<app-custom-instructions>`
containment in the system prompt — alongside the bundle's
`initialize.instructions`.

This module owns the storage half of that contract for DB Query: a single
Markdown file in the bundle's workspace root, alongside the Upjack entity
stores so it gets the same per-workspace isolation.

Storage / write / clear semantics:
  - Read returns "" when the file is missing or empty (the platform
    omits the `<app-custom-instructions>` block in that case).
  - Write replaces the file atomically (temp + rename).
  - Empty text deletes the file; a missing file is the post-condition.
  - 8 KiB UTF-8 cap mirrors the platform-side org/workspace overlay limit.
"""

from __future__ import annotations

import os
from pathlib import Path

INSTRUCTIONS_FILE = "custom-instructions.md"
MAX_INSTRUCTIONS_BYTES = 8 * 1024


def _instructions_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / INSTRUCTIONS_FILE


def read_custom_instructions(workspace_root: str | Path) -> str:
    """Return the saved instructions, or "" when none are set."""
    path = _instructions_path(workspace_root)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_custom_instructions(workspace_root: str | Path, text: str) -> dict[str, str]:
    """Persist `text` (or clear when empty). Returns a one-line status dict.

    Raises `ValueError` when `text` exceeds the 8 KiB UTF-8 byte cap. Atomic
    via temp + rename so readers never see a partially-written file.
    """
    path = _instructions_path(workspace_root)

    if text == "":
        path.unlink(missing_ok=True)
        return {"status": "cleared"}

    encoded = text.encode("utf-8")
    if len(encoded) > MAX_INSTRUCTIONS_BYTES:
        raise ValueError(
            f"Instructions exceed {MAX_INSTRUCTIONS_BYTES} byte limit (got {len(encoded)} bytes)"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_bytes(encoded)
    os.replace(tmp, path)
    return {"status": "saved"}
