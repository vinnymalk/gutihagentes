#!/usr/bin/env python3
"""OpenClaw Cyberpunk Dashboard — FastAPI + WebSocket backend."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import psutil
import requests
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import database
import auth

GATEWAY = "http://127.0.0.1:18789"
TOKEN = os.environ.get("OPENCLAW_TOKEN", "25f92f9775893e90e7d879f6415bd0d6f35a302b92d87a48")
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG", "/home/vinny/.openclaw/openclaw.json"))
STATIC_DIR = Path(__file__).parent / "static"
WS_INTERVAL = 5  # seconds between WebSocket pushes

AGENT_META = {
    "hanna":      {"emoji": "🔱", "name": "Hanna Theá",          "role": "Líder / Estrategista"},
    "pitaco":     {"emoji": "⚖️", "name": "Pítaco Nomothetes",    "role": "Tenente / Processos"},
    "socrates":   {"emoji": "🦅", "name": "Sócrates Erevnitis",   "role": "Pesquisador"},
    "aristoteles":{"emoji": "🦉", "name": "Aristóteles Synthesis","role": "Sintetizador"},
    "demostenes": {"emoji": "📜", "name": "Demóstenes Logographos","role": "Escritor"},
    "hermes":     {"emoji": "✉️", "name": "Hermes Angelos",        "role": "Mensageiro"},
}

AGENT_ORDER = ["hanna", "pitaco", "socrates", "aristoteles", "demostenes", "hermes"]


def classify_model(model: str) -> dict:
    m = model.lower()
    if "anthropic" in m or "claude" in m:
        return {"tier": "premium", "label": "Premium", "icon": "🔴"}
    if ":free" in m or "gemini" in m:
        return {"tier": "free", "label": "Free", "icon": "🟢"}
    if "deepseek" in m or "openai" in m or "gpt" in m:
        return {"tier": "api", "label": "API Key", "icon": "💰"}
    return {"tier": "unknown", "label": "?", "icon": "⚪"}


def read_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        agents = {}
        for a in cfg.get("agents", {}).get("list", []):
            aid = a.get("id", "")
            model_cfg = a.get("model", {})
            agents[aid] = {
                "id": aid,
                "primaryModel": model_cfg.get("primary", "?"),
                "fallbackModels": model_cfg.get("fallbacks", []),
                "workspace": a.get("workspace", ""),
            }
        return agents
    except Exception:
        return {}


def call_gateway(tool: str, args: dict) -> dict:
    try:
        r = requests.post(
            f"{GATEWAY}/tools/invoke",
            json={"tool": tool, "args": args},
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=8,
        )
        data = r.json()
        return data.get("result", {}).get("details") or {}
    except Exception as e:
        return {"error": str(e)}


def get_sessions() -> list[dict]:
    data = call_gateway("sessions_list", {"limit": 100})
    return data.get("sessions", [])


def sysinfo() -> dict:
    boot = psutil.boot_time()
    uptime_s = int(time.time() - boot)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=0.5)
    return {
        "uptime": f"{h}h {m}m {s}s",
        "uptimeSeconds": uptime_s,
        "cpu": cpu,
        "ram": {"used": mem.used, "total": mem.total, "percent": mem.percent},
        "disk": {"used": disk.used, "total": disk.total, "percent": disk.percent},
    }


def fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def build_payload(sessions: list[dict], config: dict, sys: dict) -> dict:
    # Group sessions by agentId, keep most recent per agent
    by_agent: dict[str, list] = {}
    for s in sessions:
        aid = s.get("agentId", "unknown")
        by_agent.setdefault(aid, []).append(s)

    agents_out = []
    for aid in AGENT_ORDER:
        meta = AGENT_META.get(aid, {"emoji": "🤖", "name": aid, "role": ""})
        cfg = config.get(aid, {})
        model = cfg.get("primaryModel", "?")
        tier_info = classify_model(model)
        agent_sessions = by_agent.get(aid, [])
        # most recent session
        latest = max(agent_sessions, key=lambda x: x.get("updatedAt", 0), default=None)
        status = "offline"
        last_action = "—"
        tokens_total = sum(s.get("totalTokens", 0) for s in agent_sessions)
        cost_total = sum(s.get("estimatedCostUsd", 0) for s in agent_sessions)
        channel = "—"
        if latest:
            st = latest.get("status", "")
            if st == "running":
                status = "active"
            elif st == "done":
                status = "idle"
            elif st == "failed":
                status = "error"
            else:
                status = "idle"
            updated = latest.get("updatedAt", 0)
            if updated:
                delta = int(time.time() * 1000 - updated)
                minutes = delta // 60000
                if minutes < 1:
                    last_action = "agora"
                elif minutes < 60:
                    last_action = f"há {minutes}m"
                else:
                    hours = minutes // 60
                    last_action = f"há {hours}h"
            channel = latest.get("lastChannel", latest.get("channel", "—"))

        agents_out.append({
            "id": aid,
            "emoji": meta["emoji"],
            "name": meta["name"],
            "role": meta["role"],
            "model": model,
            "tier": tier_info,
            "status": status,
            "lastAction": last_action,
            "channel": channel,
            "totalTokens": tokens_total,
            "totalCostUsd": round(cost_total, 6),
            "activeSessions": sum(1 for s in agent_sessions if s.get("status") == "running"),
        })

    # Active sessions table (only running or recent done)
    active = [s for s in sessions if s.get("status") in ("running",)]
    recent = sorted(sessions, key=lambda x: x.get("updatedAt", 0), reverse=True)[:10]

    sessions_out = []
    for s in recent:
        aid = s.get("agentId", "?")
        meta = AGENT_META.get(aid, {"emoji": "🤖", "name": aid})
        sessions_out.append({
            "key": s.get("key", ""),
            "agentId": aid,
            "agentEmoji": meta["emoji"],
            "agentName": meta["name"],
            "model": s.get("model", "?"),
            "tier": classify_model(s.get("model", "")),
            "status": s.get("status", "?"),
            "totalTokens": s.get("totalTokens", 0),
            "costUsd": round(s.get("estimatedCostUsd", 0), 6),
            "channel": s.get("lastChannel", s.get("channel", "?")),
            "updatedAt": s.get("updatedAt", 0),
        })

    total_cost = sum(s.get("estimatedCostUsd", 0) for s in sessions)
    total_tokens = sum(s.get("totalTokens", 0) for s in sessions)

    return {
        "ts": int(time.time() * 1000),
        "agents": agents_out,
        "sessions": sessions_out,
        "summary": {
            "activeSessions": len(active),
            "totalSessions": len(sessions),
            "totalTokens": total_tokens,
            "totalCostUsd": round(total_cost, 6),
        },
        "sys": sys,
    }


app = FastAPI(title="OpenClaw Dashboard")

# ── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    database.init_db()
    print("[APP] Database initialized")


# ── Auth helpers ──────────────────────────────────────────────────────────────
def _get_user(request: Request) -> Optional[dict]:
    token = auth.extrair_token(dict(request.headers))
    if not token:
        return None
    return auth.validar_sessao(token)


def _navigate_html(elems: list[dict]) -> str:
    links = "".join(
        f'<a href="{e["url"]}" style="color:var(--cyan);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:4px;font-size:12px">{e["label"]}</a>'
        for e in elems
    )
    nav_bar = f'<div style="display:flex;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid var(--border);margin-bottom:16px">{links}</div>'
    return nav_bar + '<div style="margin-bottom:16px"></div>'

# Serve static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html_file = STATIC_DIR / "index.html"
    if not html_file.exists():
        return HTMLResponse("<h1>OpenClaw Dashboard</h1><p>index.html not found</p>")
    html = html_file.read_text()
    # Inject navigation bar
    nav = _navigate_html([
        {"url": "/", "label": "🤖 Agentes"},
        {"url": "/nfse", "label": "📋 NFS-e"},
    ])
    if "<body>" in html:
        html = html.replace("<body>", f"<body>{nav}")
    return HTMLResponse(html)


@app.get("/api/data")
async def api_data():
    sessions = get_sessions()
    config = read_config()
    sys = sysinfo()
    return JSONResponse(build_payload(sessions, config, sys))


@app.get("/api/sysinfo")
async def api_sysinfo():
    return JSONResponse(sysinfo())


# ── NFS-e: Login ──────────────────────────────────────────────────────────


@app.get("/nfse/login", response_class=HTMLResponse)
async def nfse_login_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NFS-e — Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b0c1a;color:#d0d8e8;font-family:'Fira Code',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(0,212,255,0.2);border-radius:8px;padding:32px;max-width:400px;width:90%}
h2{color:#00d4ff;margin-bottom:20px;text-align:center}
label{color:#777;font-size:12px;display:block;margin:12px 0 4px}
input{width:100%;padding:10px;background:rgba(255,255,255,0.05);border:1px solid rgba(0,212,255,0.15);border-radius:4px;color:#d0d8e8;font-size:14px}
button{width:100%;padding:10px;background:#00d4ff;color:#0b0c1a;border:none;border-radius:4px;font-size:14px;font-weight:700;margin-top:20px;cursor:pointer}
button:hover{background:#00ffee}
.erro{color:#ff4444;font-size:12px;text-align:center;margin-top:12px}
</style></head><body>
<div class="card">
<h2>📋 NFS-e</h2>
<form method="post" action="/nfse/login">
<label>Usuário</label><input name="username" autocomplete="username" required>
<label>Senha</label><input type="password" name="senha" autocomplete="current-password" required>
<button type="submit">Entrar</button>
</form>
<div class="erro" id="erro"></div>
</div>
<script>
const params = new URLSearchParams(window.location.search);
if(params.get('erro')) document.getElementById('erro').textContent=params.get('erro');
</script>
</body></html>""")


@app.post("/nfse/login")
async def nfse_login_post(username: str = Form(...), senha: str = Form(...)):
    user = auth.verificar_login(username, senha)
    if not user:
        return RedirectResponse("/nfse/login?erro=Usu%C3%A1rio+ou+senha+inv%C3%A1lidos", status_code=302)
    token = auth.criar_sessao(user)
    resp = RedirectResponse("/nfse", status_code=302)
    resp.headers["Set-Cookie"] = auth.criar_cookie(token)
    return resp


@app.get("/nfse/logout")
async def nfse_logout():
    resp = RedirectResponse("/nfse/login", status_code=302)
    resp.headers["Set-Cookie"] = auth.limpar_cookie()
    return resp


# ── NFS-e: API ────────────────────────────────────────────────────────────────


def _req_user(request: Request) -> dict:
    u = _get_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return u


@app.get("/api/nfse/clientes")
async def api_nfse_clientes(request: Request, search: str = ""):
    _req_user(request)
    return JSONResponse(database.listar_clientes(search))


@app.post("/api/nfse/clientes")
async def api_nfse_criar_cliente(request: Request):
    u = _req_user(request)
    body = await request.json()
    cid = database.criar_cliente(body)
    database.log_acao(u["username"], "criar_cliente", json.dumps({"id": cid, "nome": body.get("nome")}))
    return JSONResponse({"id": cid, "mensagem": "Cliente criado"})


@app.get("/api/nfse/clientes/{cliente_id}")
async def api_nfse_get_cliente(request: Request, cliente_id: int):
    _req_user(request)
    c = database.get_cliente(cliente_id)
    if not c:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return JSONResponse(c)


@app.put("/api/nfse/clientes/{cliente_id}")
async def api_nfse_atualizar_cliente(request: Request, cliente_id: int):
    u = _req_user(request)
    body = await request.json()
    ok = database.atualizar_cliente(cliente_id, body)
    if not ok:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    database.log_acao(u["username"], "atualizar_cliente", json.dumps({"id": cliente_id}))
    return JSONResponse({"mensagem": "Cliente atualizado"})


@app.delete("/api/nfse/clientes/{cliente_id}")
async def api_nfse_deletar_cliente(request: Request, cliente_id: int):
    u = _req_user(request)
    if u["role"] != "admin":
        raise HTTPException(status_code=403, detail="Só admin pode excluir clientes")
    ok = database.deletar_cliente(cliente_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    database.log_acao(u["username"], "deletar_cliente", json.dumps({"id": cliente_id}))
    return JSONResponse({"mensagem": "Cliente excluído"})


@app.get("/api/nfse/notas")
async def api_nfse_notas(
    request: Request,
    cliente_id: Optional[int] = None,
    status: Optional[str] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    limit: int = 100,
):
    _req_user(request)
    return JSONResponse(database.listar_notas(cliente_id, status, data_ini, data_fim, limit))


@app.post("/api/nfse/notas")
async def api_nfse_criar_nota(request: Request):
    u = _req_user(request)
    body = await request.json()
    body["emitida_por"] = u["username"]
    nid = database.criar_nota(body)
    database.log_acao(u["username"], "criar_nota", json.dumps({"id": nid, "valor": body.get("valor")}))
    return JSONResponse({"id": nid, "mensagem": "Nota registrada"})


@app.get("/api/nfse/notas/{nota_id}")
async def api_nfse_get_nota(request: Request, nota_id: int):
    _req_user(request)
    n = database.get_nota(nota_id)
    if not n:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    return JSONResponse(n)


@app.put("/api/nfse/notas/{nota_id}")
async def api_nfse_atualizar_nota(request: Request, nota_id: int):
    u = _req_user(request)
    body = await request.json()
    ok = database.atualizar_nota(nota_id, body)
    if not ok:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    database.log_acao(u["username"], "atualizar_nota", json.dumps({"id": nota_id}))
    return JSONResponse({"mensagem": "Nota atualizada"})


@app.post("/api/nfse/emitir")
async def api_nfse_emitir(request: Request):
    """Endpoint para fluxo de emissão via Pítaco/Telegram."""
    u = _req_user(request)
    body = await request.json()
    # Validação básica
    if not body.get("cliente_id"):
        raise HTTPException(status_code=400, detail="cliente_id é obrigatório")
    if not body.get("valor"):
        raise HTTPException(status_code=400, detail="valor é obrigatório")
    body["emitida_por"] = f"pitaco-{u['username']}"
    body["status"] = "emitida"
    nid = database.criar_nota(body)
    database.log_acao(u["username"], "emitir_nota", json.dumps({"id": nid, "valor": body.get("valor")}))
    return JSONResponse({"id": nid, "mensagem": "Nota emitida com sucesso", "status": "emitida"})


@app.post("/api/nfse/cancelar/{nota_id}")
async def api_nfse_cancelar(request: Request, nota_id: int):
    u = _req_user(request)
    nota = database.get_nota(nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    database.atualizar_nota(nota_id, {"status": "cancelada"})
    database.log_acao(u["username"], "cancelar_nota", json.dumps({"id": nota_id, "numero": nota["numero"]}))
    return JSONResponse({"mensagem": "Nota cancelada", "id": nota_id})


@app.get("/api/nfse/logs")
async def api_nfse_logs(request: Request, limit: int = 50):
    _req_user(request)
    return JSONResponse(database.listar_logs(limit))


@app.get("/api/nfse/stats")
async def api_nfse_stats(request: Request):
    _req_user(request)
    clientes = database.listar_clientes()
    notas = database.listar_notas(limit=9999)
    total_notas = len(notas)
    total_valor = sum(n.get("valor", 0) or 0 for n in notas)
    emitidas = sum(1 for n in notas if n["status"] == "emitida")
    canceladas = sum(1 for n in notas if n["status"] == "cancelada")
    return JSONResponse({
        "total_clientes": len(clientes),
        "total_notas": total_notas,
        "total_valor": round(total_valor, 2),
        "emitidas": emitidas,
        "canceladas": canceladas,
    })


# ── NFS-e: Páginas ───────────────────────────────────────────────────────────


def _nfse_page(request: Request, sub_path: str) -> HTMLResponse:
    u = _get_user(request)
    if not u:
        redirect = RedirectResponse("/nfse/login")
        return HTMLResponse(
            '<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=/nfse/login"></head><body></body></html>',
            status_code=302,  headers=redirect.headers,
        )
    html_file = STATIC_DIR / sub_path
    if not html_file.exists():
        return HTMLResponse(f"<h1>Em construção</h1><p>{sub_path} ainda não foi criado</p>")
    html = html_file.read_text()
    nav = _navigate_html([
        {"url": "/", "label": "🤖 Agentes"},
        {"url": "/nfse", "label": "📋 NFS-e"},
        {"url": "/nfse/logout", "label": "🚪 Sair (" + u["username"] + ")"},
    ])
    if "<body>" in html:
        html = html.replace("<body>", f"<body>{nav}")
    return HTMLResponse(html)


@app.get("/nfse", response_class=HTMLResponse)
async def nfse_index(request: Request):
    return _nfse_page(request, "nfse/index.html")


@app.get("/nfse/notas", response_class=HTMLResponse)
async def nfse_notas(request: Request):
    return _nfse_page(request, "nfse/notas.html")


@app.get("/nfse/clientes", response_class=HTMLResponse)
async def nfse_clientes(request: Request):
    return _nfse_page(request, "nfse/clientes.html")



# ── Push endpoint (for local pusher → Render) ──────────────────────────────────
_remote_data: dict = {}

@app.post("/api/push")
async def receive_push(data: dict):
    """Recebe dados do pusher local e armazena."""
    global _remote_data
    _remote_data = data
    # Notify all WebSocket clients
    for ws in _connections:
        try:
            await ws.send_json({"type": "data", **data})
        except Exception:
            pass
    return {"ok": True}

@app.get("/api/data/remote")
async def get_remote_data():
    """Retorna os dados pusheados (Render mode)."""
    global _remote_data
    if _remote_data:
        return _remote_data
    return {"note": "No pushed data yet. Run pusher.py on the gateway machine."}


# ── WebSocket ─────────────────────────────────────────────────────────────────
_connections: list[WebSocket] = []


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connections.append(ws)
    try:
        while True:
            try:
                sessions = get_sessions()
                config = read_config()
                sys = sysinfo()
                payload = build_payload(sessions, config, sys)
                await ws.send_text(json.dumps(payload))
            except Exception as e:
                await ws.send_text(json.dumps({"error": str(e)}))
            await asyncio.sleep(WS_INTERVAL)
    except WebSocketDisconnect:
        _connections.remove(ws)
    except Exception:
        if ws in _connections:
            _connections.remove(ws)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    database.init_db()
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
