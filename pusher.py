#!/usr/bin/env python3
"""Pusher — envia dados do gateway local pro dashboard no Render."""

import asyncio
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

GATEWAY_URL = "http://127.0.0.1:18789"
GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "25f92f9775893e90e7d879f6415bd0d6f35a302b92d87a48")
RENDER_URL = os.environ.get("RENDER_DASHBOARD_URL", "https://openclaw-dashboard-kcl1.onrender.com")
PUSH_INTERVAL = 5  # seconds

def call_gateway(tool: str, args: dict) -> dict:
    """Chama uma tool do gateway."""
    import http.client
    payload = json.dumps({"tool": tool, "args": args}).encode()
    req = Request(
        f"{GATEWAY_URL}/tools/invoke",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GATEWAY_TOKEN}"
        },
        method="POST"
    )
    try:
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

async def collect_and_push():
    """Coleta dados do gateway e envia pro Render."""
    while True:
        try:
            # 1. Get sessions
            sessions = call_gateway("sessions_list", {"limit": 100})
            
            # 2. Get system info
            sysinfo = {
                "uptime": open("/proc/uptime").read().split()[0],
                "cpu": psutil_percent(),
                "memory": get_memory(),
                "disk": get_disk()
            }
            
            # 3. Build agent status from sessions
            agents = {}
            session_list = sessions if isinstance(sessions, list) else []
            
            for sess in session_list:
                s = sess.get("session", sess)
                agent_id = s.get("agentId", "?")
                if agent_id not in agents:
                    agents[agent_id] = {
                        "id": agent_id,
                        "status": "idle",
                        "model": s.get("model", "?"),
                        "sessions": [],
                        "totalTokens": 0
                    }
                agents[agent_id]["sessions"].append({
                    "id": s.get("sessionId", ""),
                    "model": s.get("model", "?"),
                    "status": "active",
                    "totalTokens": s.get("totalTokens", 0),
                    "updatedAt": s.get("updatedAt", 0)
                })
                agents[agent_id]["status"] = "active"
                agents[agent_id]["totalTokens"] += s.get("totalTokens", 0)
            
            # 4. Build payload
            payload = {
                "ts": int(time.time() * 1000),
                "agents": [
                    {"id": aid, **adata}
                    for aid, adata in agents.items()
                ],
                "sessions": session_list,
                "sys": sysinfo
            }
            
            # 5. Push to Render
            req = Request(
                f"{RENDER_URL}/api/push",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    print(f"[{time.strftime('%H:%M:%S')}] Push OK — {len(agents)} agents")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] Push response: {result}")
                    
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Error: {e}")
        
        await asyncio.sleep(PUSH_INTERVAL)

def psutil_percent():
    """CPU percent via /proc/stat."""
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        vals = [int(x) for x in line.split()[1:]]
        total = sum(vals)
        idle = vals[3]
        return round(100 * (1 - idle/total), 1) if total > 0 else 0
    except:
        return 0

def get_memory():
    try:
        with open('/proc/meminfo') as f:
            lines = f.readlines()
        mem_total = int([l for l in lines if 'MemTotal' in l][0].split()[1]) * 1024
        mem_avail = int([l for l in lines if 'MemAvailable' in l][0].split()[1]) * 1024
        return {"used": mem_total - mem_avail, "total": mem_total, "percent": round(100 * (1 - mem_avail/mem_total), 1)}
    except:
        return {}

def get_disk():
    import shutil
    usage = shutil.disk_usage('/')
    return {"used": usage.used, "total": usage.total, "percent": round(100 * usage.used / usage.total, 1)}

if __name__ == "__main__":
    print("🚀 Pusher iniciado — enviando dados ao Render a cada 5s")
    print(f"   Gateway: {GATEWAY_URL}")
    print(f"   Dashboard: {RENDER_URL}")
    print("   Pressione Ctrl+C para parar\n")
    asyncio.run(collect_and_push())
