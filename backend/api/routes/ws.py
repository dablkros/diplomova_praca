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

OPTICAL_INTERFACE_TYPES = {
    "1000base-x-sfp",
    "10gbase-x-sfpp",
    "25gbase-x-sfp28",
    "40gbase-x-qsfp+",
    "100gbase-x-qsfp28",
    "sfp",
    "sfp+",
    "qsfp+",
    "qsfp28",
}


def is_optical_interface(netbox_type: str | None) -> bool:
    if not netbox_type:
        return False
    return netbox_type.strip().lower() in OPTICAL_INTERFACE_TYPES


@router.websocket("/ws/optics")
async def ws_optics(ws: WebSocket):
    await ws.accept()
    params = ws.query_params

    host = params.get("host")
    username = params.get("username")
    password = params.get("password")
    interface = params.get("interface")
    interface_type = params.get("interface_type")  # typ z NetBoxu

    if not host or not interface:
        await ws.send_json({"error": "Missing host or interface"})
        await ws.close()
        return

    if not is_optical_interface(interface_type):
        await ws.send_json({
            "status": "not_applicable",
            "host": host,
            "interface": interface,
            "message": "Podľa SoT ide o neoptický interface, optický monitoring sa nevykonáva."
        })
        await ws.close()
        return

    user, pwd = resolve_creds(username, password)
    client = DeviceClient(host, user, pwd)

    try:
        client._ensure_ssh()

        while True:
            optics = client.show_optics(interface)

            if optics.get("found"):
                await ws.send_json({
                    "status": "ok",
                    "host": host,
                    "interface": interface,
                    "temperature": optics.get("temperature"),
                    "voltage": optics.get("voltage"),
                    "tx_power": optics.get("tx_power"),
                    "rx_power": optics.get("rx_power"),
                    "tx_bias": optics.get("tx_bias"),
                })
            else:
                await ws.send_json({
                    "status": "no_data",
                    "host": host,
                    "interface": interface,
                    "message": optics.get("message", "Optické dáta nie sú dostupné.")
                })

            await asyncio.sleep(5)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        await ws.send_json({
            "status": "error",
            "error": str(e),
            "trace": tb
        })
    finally:
        try:
            client.disconnect()
        except Exception:
            pass