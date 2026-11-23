#!/usr/bin/env bash
set -Eeuo pipefail

# The validated 4.4.3-2 builder is stored compressed because it was recovered
# outside the incomplete repository. This wrapper verifies and executes it from
# the repository root so its path-dependent behavior remains correct.

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE="$ROOT_DIR/.source-recovery/build-telesaved-deb.sh.gz"
MATERIALIZED="$ROOT_DIR/.build-telesaved-deb-4.4.3-2.materialized.sh"
EXPECTED_SHA256="4c377555aaf9e2a6da65bb9464528288243258051e0f11188bca28738881e1fb"

if [[ ! -f "$ARCHIVE" ]]; then
    printf 'Missing TeleSaved builder archive: %s\n' "$ARCHIVE" >&2
    exit 1
fi

gzip -dc -- "$ARCHIVE" > "$MATERIALIZED"
printf '%s  %s\n' "$EXPECTED_SHA256" "$MATERIALIZED" | sha256sum -c -
chmod 0755 "$MATERIALIZED"

exec "$MATERIALIZED" "$@"
