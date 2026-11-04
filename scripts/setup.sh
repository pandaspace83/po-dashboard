#!/usr/bin/env bash
# macOS setup for PO Dashboard. Safe to re-run. Asks before downloading the 9 GB model.
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(uname)" = "Darwin" ] || { echo "PO Dashboard supports macOS only (DEC-18)."; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: brew install uv"; exit 1; }
command -v ollama >/dev/null || { echo "Install Ollama first: brew install ollama"; exit 1; }
command -v tesseract >/dev/null || echo "Note: Tesseract not found (needed for scanned PDFs): brew install tesseract"

uv sync

if [ -d .git ]; then
  cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
  echo "Installed the pre-commit guard."
fi

MODEL="qwen2.5:14b-instruct"
# Read the list first: `ollama list | grep -q` can fail under pipefail when grep stops reading early.
installed=$(ollama list 2>/dev/null || true)
if grep -q "^$MODEL[[:space:]]" <<<"$installed"; then
  echo "Model $MODEL is already present."
else
  # Printed with echo: `read -p` shows its prompt only on a terminal, and the size must always be stated.
  echo "The model $MODEL is not installed. It is about 9 GB."
  read -r -p "Download it now? [y/N] " yn || yn=n
  if [ "$yn" = "y" ]; then
    ollama pull "$MODEL"
  else
    echo "Skipped. Pull it later with: ollama pull $MODEL"
  fi
fi
echo "Done. Start with: uv run po-dashboard serve"
