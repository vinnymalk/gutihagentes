#!/usr/bin/env python3
"""OpenClaw Cyberpunk Dashboard — FastAPI + WebSocket backend."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import psutil
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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


async def call_gateway(tool: str, args: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"{GATEWAY}/tools/invoke",
                json={"tool": tool, "args": args},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            data = r.json()
            return data.get("result", {}).get("details") or {}
    except Exception as e:
        return {"error": str(e)}


async def get_sessions() -> list[dict]:
    data = await call_gateway("sessions_list", {"limit": 100})
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

# Serve static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text())
    return HTMLResponse("<h1>OpenClaw Dashboard</h1><p>index.html not found</p>")


@app.get("/api/data")
async def api_data():
    sessions = await get_sessions()
    config = read_config()
    sys = sysinfo()
    return JSONResponse(build_payload(sessions, config, sys))


@app.get("/api/sysinfo")
async def api_sysinfo():
    return JSONResponse(sysinfo())


# WebSocket connections pool
_connections: list[WebSocket] = []


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _connections.append(ws)
    try:
        while True:
            try:
                sessions = await get_sessions()
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
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
