#!/usr/bin/env python3
"""Authentication middleware for NFS-e system — Basic Auth + session."""

import hashlib
import os
import secrets
import time
from typing import Optional

from database import db_cursor

# In-memory session store (fine for single-instance Render)
_sessions: dict[str, dict] = {}


def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


def verificar_login(username: str, senha: str) -> Optional[dict]:
    h = hash_senha(senha)
    with db_cursor() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM usuarios WHERE username = ? AND hash_senha = ?",
            (username, h),
        ).fetchone()
        return dict(row) if row else None


def criar_sessao(user: dict) -> str:
    token = secrets.token_hex(32)
    _sessions[token] = {
        "user": user,
        "created_at": time.time(),
        "expires_at": time.time() + 86400 * 7,  # 7 days
    }
    return token


def validar_sessao(token: str) -> Optional[dict]:
    session = _sessions.get(token)
    if not session:
        return None
    if time.time() > session["expires_at"]:
        del _sessions[token]
        return None
    return session["user"]


def destruir_sessao(token: str):
    _sessions.pop(token, None)


def extrair_token(request_headers: dict) -> Optional[str]:
    """Extract session token from Cookie header."""
    cookies = request_headers.get("cookie", "")
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("nfse_token="):
            return part[11:]
    # Also check Authorization header as fallback
    auth = request_headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def criar_cookie(token: str) -> str:
    return (
        f"nfse_token={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Lax"
    )


def limpar_cookie() -> str:
    return "nfse_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
