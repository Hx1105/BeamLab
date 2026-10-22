#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
${PYTHON:-python3.13} -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p runs reports evidence runtime

: "${AGH_SOURCE_DIR:?Set AGH_SOURCE_DIR to the external AGH workspace containing the MHS Python SDK}"
.venv/bin/python -m pip install --no-deps -e "$AGH_SOURCE_DIR/packages/mhs/python"
