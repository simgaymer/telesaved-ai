# TeleSaved source recovery archive

This directory contains the source files that were absent from the repository when it was audited on July 28, 2026.

The files are compressed only to preserve their exact recovered bytes while working within the repository connector's transfer limits:

- `app.py.gz.part-*` — recovered backend, split into ordered chunks.
- `index.html.gz` — exact current frontend recovered from the project's connected Google Drive.
- `build-telesaved-deb.sh.gz` — corrected TeleSaved AI 4.4.3-2 Debian package builder.

## Materializing ordinary files

From the repository root, run:

```bash
python3 tools/materialize-recovered-source.py
```

The script verifies every SHA-256 digest before replacing the loader/wrapper files with the ordinary source files.

## Verified SHA-256 digests

```text
app.py
0b5f6202fc2cad21c9c8edd811dd29ae2b8b53aa2a43eb18ec7ca8a4566c17b4

frontend/index.html
12fffa46684630af4a14eff14a600ab302c916cae9d117c094a73da6c2415ec0

build-telesaved-deb.sh
4c377555aaf9e2a6da65bb9464528288243258051e0f11188bca28738881e1fb
```

## Provenance warning

The frontend is the exact current copy from the project's connected Drive. The backend was reconstructed from the project's 151-page printed `app.py` snapshot because no complete backend source file was present in GitHub or connected storage. It contains 515 functions and 133 route declarations and passes Python compilation, but it is not claimed to be byte-for-byte identical to the original file that produced the printout.
