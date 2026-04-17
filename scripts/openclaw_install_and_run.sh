#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/tianchen95hk/us-equities-forecast.git}"
REPO_DIR="${REPO_DIR:-$HOME/us-equities-forecast}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_ARGS="${RUN_ARGS:---live --output-style simple --output-lang zh}"
USE_VENV="${USE_VENV:-false}"
PIP_USER_INSTALL="${PIP_USER_INSTALL:-true}"

to_bool() {
  case "${1,,}" in
    1|true|yes|y|on) echo "true" ;;
    *) echo "false" ;;
  esac
}

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"

git fetch origin
git checkout main
git pull --ff-only origin main

if [ "$(to_bool "$USE_VENV")" = "true" ]; then
  if [ ! -d ".venv" ]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PIP_INSTALL_CMD=("$PYTHON_BIN" -m pip install)
if [ "$(to_bool "$PIP_USER_INSTALL")" = "true" ] && [ "$(to_bool "$USE_VENV")" = "false" ]; then
  PIP_INSTALL_CMD+=("--user")
fi

"${PIP_INSTALL_CMD[@]}" --upgrade pip
"${PIP_INSTALL_CMD[@]}" -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[INFO] .env created from .env.example. Fill API keys before live run if needed."
fi

"$PYTHON_BIN" -m app.main run $RUN_ARGS
