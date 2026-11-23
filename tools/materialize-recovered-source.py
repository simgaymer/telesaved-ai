#!/usr/bin/env python3
"""Materialize the recovered TeleSaved source into ordinary repository files."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
from pathlib import Path

APP_SHA256 = "0b5f6202fc2cad21c9c8edd811dd29ae2b8b53aa2a43eb18ec7ca8a4566c17b4"
FRONTEND_SHA256 = "12fffa46684630af4a14eff14a600ab302c916cae9d117c094a73da6c2415ec0"
BUILDER_SHA256 = "4c377555aaf9e2a6da65bb9464528288243258051e0f11188bca28738881e1fb"


def verify(data: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(
            f"{label} SHA-256 mismatch: expected {expected}, received {actual}"
        )


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.materializing")
    temporary.write_bytes(data)
    if mode is not None:
        os.chmod(temporary, mode)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore ordinary TeleSaved source files from .source-recovery"
    )
    parser.add_argument(
        "--remove-archive",
        action="store_true",
        help="delete .source-recovery after successful materialization",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    recovery = root / ".source-recovery"

    parts = sorted(recovery.glob("app.py.gz.part-*"))
    if not parts:
        raise SystemExit(f"No backend chunks found under {recovery}")

    app = gzip.decompress(b"".join(part.read_bytes() for part in parts))
    frontend = gzip.decompress((recovery / "index.html.gz").read_bytes())
    builder = gzip.decompress((recovery / "build-telesaved-deb.sh.gz").read_bytes())

    verify(app, APP_SHA256, "app.py")
    verify(frontend, FRONTEND_SHA256, "frontend/index.html")
    verify(builder, BUILDER_SHA256, "build-telesaved-deb.sh")

    compile(app, str(root / "app.py"), "exec")

    atomic_write(root / "app.py", app, 0o755)
    atomic_write(root / "frontend" / "index.html", frontend, 0o644)
    atomic_write(root / "build-telesaved-deb.sh", builder, 0o755)

    print("Materialized and verified:")
    print(f"  app.py                 {APP_SHA256}")
    print(f"  frontend/index.html    {FRONTEND_SHA256}")
    print(f"  build-telesaved-deb.sh {BUILDER_SHA256}")

    if args.remove_archive:
        shutil.rmtree(recovery)
        print(f"Removed recovery archive: {recovery}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
