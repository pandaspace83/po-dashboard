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
if ollama list 2>/dev/null | grep -q "^$MODEL"; then
  echo "Model $MODEL is already present."
else
  read -r -p "Download $MODEL (about 9 GB)? [y/N] " yn
  [ "$yn" = "y" ] && ollama pull "$MODEL" || echo "Skipped. Pull it later with: ollama pull $MODEL"
fi
echo "Done. Start with: uv run po-dashboard serve"
