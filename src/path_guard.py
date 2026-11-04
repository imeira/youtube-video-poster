"""Lightweight lexical filesystem guard shared across storage boundaries."""

from __future__ import annotations

import os
from pathlib import Path


def _is_reparse_point(path: Path) -> bool:
    """Treat Windows reparse points and POSIX symlinks as unsafe indirection."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & 0x400)


def reject_reparse_path(path: Path) -> Path:
    """Reject a lexical path or existing ancestor before it is resolved or used."""
    lexical = Path(os.path.abspath(os.fspath(path)))
    for candidate in (lexical, *lexical.parents):
        if _is_reparse_point(candidate):
            raise ValueError("local input rejects symlink or reparse-point paths")
    return lexical