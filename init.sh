#!/usr/bin/env bash
# Backwards-compatible entry point.  The implementation now lives in the
# provider-based Python package under src/urd_installer.
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${SCRIPT_DIR}/bootstrap.sh" "$@"
