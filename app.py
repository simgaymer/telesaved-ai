#!/usr/bin/env python3
"""TeleSaved AI source loader.

The complete recovered backend is stored as verified compressed chunks in
`.source-recovery/`. This loader verifies and executes that source, while also
materializing the exact current frontend before the web server starts.

Run `python3 tools/materialize-recovered-source.py` to replace this loader and
the packaging wrapper with ordinary, fully materialized source files.
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_RECOVERY = _ROOT / ".source-recovery"
_APP_SHA256 = "0b5f6202fc2cad21c9c8edd811dd29ae2b8b53aa2a43eb18ec7ca8a4566c17b4"
_FRONTEND_SHA256 = "12fffa46684630af4a14eff14a600ab302c916cae9d117c094a73da6c2415ec0"


def _verify(data: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"Recovered {label} failed SHA-256 validation: "
            f"expected {expected}, received {actual}"
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


_parts = sorted(_RECOVERY.glob("app.py.gz.part-*"))
if not _parts:
    raise RuntimeError(
        "TeleSaved recovery chunks are missing. Expected "
        f"{_RECOVERY}/app.py.gz.part-*"
    )

_backend = gzip.decompress(b"".join(part.read_bytes() for part in _parts))
_verify(_backend, _APP_SHA256, "backend")

_frontend_archive = _RECOVERY / "index.html.gz"
if not _frontend_archive.is_file():
    raise RuntimeError(f"TeleSaved frontend archive is missing: {_frontend_archive}")
_frontend = gzip.decompress(_frontend_archive.read_bytes())
_verify(_frontend, _FRONTEND_SHA256, "frontend")

_frontend_target = _ROOT / "frontend" / "index.html"
if (
    not _frontend_target.is_file()
    or hashlib.sha256(_frontend_target.read_bytes()).hexdigest() != _FRONTEND_SHA256
):
    _atomic_write(_frontend_target, _frontend)

exec(compile(_backend, str(_ROOT / "app.py"), "exec"), globals(), globals())
