#!/bin/bash
# Sobe o dashboard na porta 8765 e opcionalmente abre o localtunnel
set -e
cd "$(dirname "$0")"

PORT=${PORT:-8765}

echo "[dashboard] Subindo FastAPI na porta $PORT..."
exec .venv/bin/python app.py
