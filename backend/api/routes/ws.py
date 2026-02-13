from __future__ import annotations

import asyncio
import traceback

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.services.device_service import resolve_creds



from backend.netconf_ops import DeviceClient

router = APIRouter(tags=["ws"])

@router.websocket("/ws/counters")
async def ws_counters(ws: WebSocket):
    await ws.accept()
    params = ws.query_params
    host      = params.get("host")
    username  = params.get("username")
    password  = params.get("password")
    interface = params.get("interface")
    if not host or not interface:
        await ws.send_json({"error": "Missing host or interface"})
        await ws.close()
        return

    user, pwd = resolve_creds(username, password)
    client = DeviceClient(host, user, pwd)
    try:
        client._ensure_ssh()
        while True:
            counters = client.show_counters(interface)
            if counters:
                entry = counters[0]
                await ws.send_json({
                    "input":  int(entry['INPUT_ERRORS']),
                    "output": int(entry['OUTPUT_ERRORS'])
                })
            else:
                await ws.send_json({"error": "No data"})
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        await ws.send_json({"error": str(e), "trace": tb})
    finally:
        client.close()

@router.websocket("/ws/live-counters")
async def ws_live_counters(ws: WebSocket):
    await ws.accept()
    params = ws.query_params
    host      = params.get("host")
    username  = params.get("username")
    password  = params.get("password")
    interface = params.get("interface")
    if not all([host, username, password, interface]):
        await ws.send_json({"error": "Missing host or interface"})
        await ws.close()
        return

    user, pwd = resolve_creds(username, password)
    client = DeviceClient(host, user, pwd)
    try:
        client._ensure_ssh()
        while True:
            counters = client.show_counters(interface)
            if counters:
                entry = counters[0]
                inp  = int(entry.get("INPUT_PACKETS", 0))
                outp = int(entry.get("OUTPUT_PACKETS", 0))
                await ws.send_json({"input": inp, "output": outp})
            else:
                await ws.send_json({"error": "No data"})
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        await ws.send_json({"error": str(e), "trace": tb})
    finally:
        client.close()
