#!/usr/bin/env python3
"""SQLite database models for NFS-e system."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("DB_PATH", "/home/vinny/.openclaw/workspace-hanna/dashboard-app/data/nfse.db"))


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_cursor() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                hash_senha TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at INTEGER NOT NULL DEFAULT (unixepoch('now'))
            );

            CREATE TABLE IF NOT EXISTS clientes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                telefone TEXT DEFAULT '',
                cpf_cnpj TEXT DEFAULT '',
                endereco TEXT DEFAULT '',
                observacoes TEXT DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
                updated_at INTEGER NOT NULL DEFAULT (unixepoch('now'))
            );

            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id INTEGER NOT NULL,
                numero TEXT DEFAULT '',
                data_emissao TEXT DEFAULT '',
                valor REAL DEFAULT 0.0,
                servico TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'emitida',
                protocolo TEXT DEFAULT '',
                observacoes TEXT DEFAULT '',
                emitida_por TEXT DEFAULT 'user',
                created_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
                updated_at INTEGER NOT NULL DEFAULT (unixepoch('now')),
                FOREIGN KEY (cliente_id) REFERENCES clientes(id)
            );

            CREATE TABLE IF NOT EXISTS log_acoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                acao TEXT NOT NULL,
                detalhes TEXT DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (unixepoch('now'))
            );
        """)

        # Create default users if table was just created
        existing = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        if existing == 0:
            import hashlib
            admin_hash = hashlib.sha256("admin123".encode()).hexdigest()
            irma_hash = hashlib.sha256("irma123".encode()).hexdigest()
            conn.execute(
                "INSERT INTO usuarios (username, hash_senha, role) VALUES (?, ?, ?)",
                ("admin", admin_hash, "admin"),
            )
            conn.execute(
                "INSERT INTO usuarios (username, hash_senha, role) VALUES (?, ?, ?)",
                ("irma", irma_hash, "user"),
            )

    print(f"[DB] Inicializado: {DB_PATH}")


# ── CRUD Clientes ─────────────────────────────────────────────────────────────

def listar_clientes(search: str = "") -> list[dict]:
    with db_cursor() as conn:
        if search:
            rows = conn.execute(
                "SELECT * FROM clientes WHERE nome LIKE ? OR cpf_cnpj LIKE ? ORDER BY nome",
                (f"%{search}%", f"%{search}%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM clientes ORDER BY nome").fetchall()
        return [dict(r) for r in rows]


def get_cliente(cliente_id: int) -> Optional[dict]:
    with db_cursor() as conn:
        r = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        return dict(r) if r else None


def criar_cliente(dados: dict) -> int:
    with db_cursor() as conn:
        cur = conn.execute(
            "INSERT INTO clientes (nome, telefone, cpf_cnpj, endereco, observacoes) VALUES (?, ?, ?, ?, ?)",
            (
                dados.get("nome", ""),
                dados.get("telefone", ""),
                dados.get("cpf_cnpj", ""),
                dados.get("endereco", ""),
                dados.get("observacoes", ""),
            ),
        )
        return cur.lastrowid


def atualizar_cliente(cliente_id: int, dados: dict) -> bool:
    with db_cursor() as conn:
        campos = []
        valores = []
        for k in ("nome", "telefone", "cpf_cnpj", "endereco", "observacoes"):
            if k in dados:
                campos.append(f"{k} = ?")
                valores.append(dados[k])
        if not campos:
            return False
        campos.append("updated_at = unixepoch('now')")
        valores.append(cliente_id)
        cur = conn.execute(
            f"UPDATE clientes SET {', '.join(campos)} WHERE id = ?", valores
        )
        return cur.rowcount > 0


def deletar_cliente(cliente_id: int) -> bool:
    with db_cursor() as conn:
        cur = conn.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))
        return cur.rowcount > 0


# ── CRUD Notas ────────────────────────────────────────────────────────────────

def listar_notas(
    cliente_id: Optional[int] = None,
    status: Optional[str] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    with db_cursor() as conn:
        query = "SELECT n.*, c.nome AS cliente_nome FROM notas n LEFT JOIN clientes c ON n.cliente_id = c.id WHERE 1=1"
        params = []
        if cliente_id:
            query += " AND n.cliente_id = ?"
            params.append(cliente_id)
        if status:
            query += " AND n.status = ?"
            params.append(status)
        if data_ini:
            query += " AND n.data_emissao >= ?"
            params.append(data_ini)
        if data_fim:
            query += " AND n.data_emissao <= ?"
            params.append(data_fim)
        query += " ORDER BY n.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_nota(nota_id: int) -> Optional[dict]:
    with db_cursor() as conn:
        r = conn.execute(
            "SELECT n.*, c.nome AS cliente_nome FROM notas n LEFT JOIN clientes c ON n.cliente_id = c.id WHERE n.id = ?",
            (nota_id,),
        ).fetchone()
        return dict(r) if r else None


def criar_nota(dados: dict) -> int:
    with db_cursor() as conn:
        cur = conn.execute(
            """INSERT INTO notas
               (cliente_id, numero, data_emissao, valor, servico, status, protocolo, observacoes, emitida_por)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dados.get("cliente_id", 0),
                dados.get("numero", ""),
                dados.get("data_emissao", ""),
                dados.get("valor", 0.0),
                dados.get("servico", ""),
                dados.get("status", "emitida"),
                dados.get("protocolo", ""),
                dados.get("observacoes", ""),
                dados.get("emitida_por", "user"),
            ),
        )
        return cur.lastrowid


def atualizar_nota(nota_id: int, dados: dict) -> bool:
    with db_cursor() as conn:
        campos = []
        valores = []
        for k in ("cliente_id", "numero", "data_emissao", "valor", "servico", "status", "protocolo", "observacoes"):
            if k in dados:
                campos.append(f"{k} = ?")
                valores.append(dados[k])
        if not campos:
            return False
        campos.append("updated_at = unixepoch('now')")
        valores.append(nota_id)
        cur = conn.execute(
            f"UPDATE notas SET {', '.join(campos)} WHERE id = ?", valores
        )
        return cur.rowcount > 0


def log_acao(usuario: str, acao: str, detalhes: str = ""):
    with db_cursor() as conn:
        conn.execute(
            "INSERT INTO log_acoes (usuario, acao, detalhes) VALUES (?, ?, ?)",
            (usuario, acao, detalhes),
        )


def listar_logs(limit: int = 50) -> list[dict]:
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT * FROM log_acoes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print("[DB] Database initialized successfully.")
