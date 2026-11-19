#!/usr/bin/env bash
# Stages separate services; never stops or changes existing services/firewalls.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 waterwall-install.py end
