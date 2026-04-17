#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/tianchen95hk/us-equities-forecast.git}"
REPO_DIR="${REPO_DIR:-$HOME/us-equities-forecast}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_ARGS="${RUN_ARGS:---live --output-style simple --output-lang zh}"

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"

git fetch origin
git checkout main
git pull --ff-only origin main

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[INFO] .env created from .env.example. Fill API keys before live run if needed."
fi

python -m app.main run $RUN_ARGS
