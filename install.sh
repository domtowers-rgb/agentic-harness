#!/usr/bin/env bash
# Installs the Agentic LLM Harness: clones the repo, creates a virtualenv,
# and installs dependencies so it's ready to run.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/domtowers-rgb/agentic-harness/main/install.sh | bash
#   ./install.sh [install-dir]

set -euo pipefail

REPO_URL="https://github.com/domtowers-rgb/agentic-harness.git"
INSTALL_DIR="${1:-agentic-harness}"

command -v git >/dev/null 2>&1 || { echo "error: git is required but not found" >&2; exit 1; }

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
# On Windows, "python"/"python3" on PATH can be a non-functional Microsoft
# Store alias stub that exists but fails when actually run - the check above
# guards against picking one of those.
[ -n "$PYTHON" ] || { echo "error: a working python3 install is required but was not found" >&2; exit 1; }

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating existing install in $INSTALL_DIR ..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "Cloning $REPO_URL into $INSTALL_DIR ..."
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "Creating virtual environment ..."
"$PYTHON" -m venv venv

if [ -f venv/bin/activate ]; then
  VENV_BIN="venv/bin"
else
  VENV_BIN="venv/Scripts"
fi

echo "Installing dependencies ..."
"$VENV_BIN/python" -m pip install --quiet --upgrade pip
"$VENV_BIN/python" -m pip install --quiet -r requirements.txt

cat <<EOF

Installed in: $(pwd)

To run it (mock backend, no external model needed):
  cd $INSTALL_DIR
  $VENV_BIN/python -m agentic_harness.main

Then open http://127.0.0.1:8000/ in a browser.

To connect to a local model server (LM Studio, Ollama, etc.) instead, either
set AGENTIC_MODEL=openai and OPENAI_BASE_URL before starting the server, or
just start it and use the "Connect" box in the web UI. See README.md for
all environment variables.
EOF
