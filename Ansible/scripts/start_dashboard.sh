#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -x .venv/bin/python ]]; then
  echo "Create the environment first: python3 -m venv .venv" >&2
  echo "Then install dependencies: .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi
exec .venv/bin/python dashboard/app.py
