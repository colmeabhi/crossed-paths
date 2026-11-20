#!/usr/bin/env bash
# Download Jeffrey Lancaster's Game of Thrones dataset into data/raw (idempotent).
# Source: https://github.com/jeffreylancaster/game-of-thrones — cite the repo if you use it.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="data/raw/game-of-thrones"
if [ -d "$DEST/data" ]; then
  echo "dataset already present at $DEST"
else
  git clone --depth 1 https://github.com/jeffreylancaster/game-of-thrones.git "$DEST"
fi
