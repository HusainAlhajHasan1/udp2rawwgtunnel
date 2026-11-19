#!/usr/bin/env bash
# Stages separate services; never switches the production WireGuard endpoint.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 waterwall-install.py middle
