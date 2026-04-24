from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.clients.netbox_client import NetBoxClient
from backend.drivers.errors import CapabilityNotSupportedError
from backend.drivers.guards import require_capability
from backend.services.device_service import make_driver_from_identity

router = APIRouter(tags=["ws"])
netbox = NetBoxClient()

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


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_optical_interface(netbox_type: str | None) -> bool:
    if not netbox_type:
        return False
    return netbox_type.strip().lower() in OPTICAL_INTERFACE_TYPES


def _make_driver_from_query(params):
    driver, host, device_name = make_driver_from_identity(
        netbox,
        device_name=params.get("device_name"),
        host=params.get("host"),
        username=params.get("username"),
        password=params.get("password"),
    )
    return driver, host, device_name


async def _send_unexpected_error(ws: WebSocket, error: Exception) -> None:
    await ws.send_json(
        {
            "status": "error",
            "error": str(error),
            "trace": traceback.format_exc(),
        }
    )


async def _stream_counter_metric(
    ws: WebSocket,
    interface: str,
    extractor: Callable[[dict], tuple[int, int]],
) -> None:
    driver = None
    try:
        params = ws.query_params
        driver, host, device_name = _make_driver_from_query(params)
        require_capability(driver, "supports_counters", "Toto zariadenie nepodporuje counters.")

        while True:
            counters = driver.get_interface_counters(interface)
            if counters:
                input_value, output_value = extractor(counters[0])
                await ws.send_json(
                    {
                        "status": "ok",
                        "host": host,
                        "device_name": device_name,
                        "interface": interface,
                        "input": input_value,
                        "output": output_value,
                    }
                )
            else:
                await ws.send_json(
                    {
                        "status": "no_data",
                        "host": host,
                        "device_name": device_name,
                        "interface": interface,
                        "message": "No data",
                    }
                )
            await asyncio.sleep(5)

    except CapabilityNotSupportedError as e:
        await ws.send_json({"status": "not_supported", "error": e.message})
        await ws.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await _send_unexpected_error(ws, e)
    finally:
        if driver:
            driver.close()


@router.websocket("/ws/counters")
async def ws_counters(ws: WebSocket):
    await ws.accept()
    interface = ws.query_params.get("interface")

    if not interface:
        await ws.send_json({"error": "Missing interface"})
        await ws.close()
        return

    await _stream_counter_metric(
        ws,
        interface,
        extractor=lambda entry: (
            safe_int(entry.get("INPUT_ERRORS")),
            safe_int(entry.get("OUTPUT_ERRORS")),
        ),
    )


@router.websocket("/ws/live-counters")
async def ws_live_counters(ws: WebSocket):
    await ws.accept()
    interface = ws.query_params.get("interface")

    if not interface:
        await ws.send_json({"error": "Missing interface"})
        await ws.close()
        return

    await _stream_counter_metric(
        ws,
        interface,
        extractor=lambda entry: (
            safe_int(entry.get("INPUT_PACKETS")),
            safe_int(entry.get("OUTPUT_PACKETS")),
        ),
    )


@router.websocket("/ws/optics")
async def ws_optics(ws: WebSocket):
    await ws.accept()
    params = ws.query_params

    interface = params.get("interface")
    interface_type = params.get("interface_type")

    if not interface:
        await ws.send_json({"error": "Missing interface"})
        await ws.close()
        return

    if not is_optical_interface(interface_type):
        await ws.send_json(
            {
                "status": "not_applicable",
                "host": params.get("host"),
                "device_name": params.get("device_name"),
                "interface": interface,
                "message": "Podľa SoT ide o neoptický interface, optický monitoring sa nevykonáva.",
            }
        )
        await ws.close()
        return

    driver = None
    try:
        driver, host, device_name = _make_driver_from_query(params)

        require_capability(
            driver,
            "supports_optics",
            "Tento driver nepodporuje optický monitoring.",
        )

        while True:
            optics = driver.show_optics(interface)

            if optics.get("found"):
                await ws.send_json(
                    {
                        "status": "ok",
                        "host": host,
                        "device_name": device_name,
                        "interface": interface,
                        "temperature": optics.get("temperature"),
                        "voltage": optics.get("voltage"),
                        "tx_power": optics.get("tx_power"),
                        "rx_power": optics.get("rx_power"),
                        "tx_bias": optics.get("tx_bias"),
                    }
                )
            else:
                await ws.send_json(
                    {
                        "status": "no_data",
                        "host": host,
                        "device_name": device_name,
                        "interface": interface,
                        "message": optics.get("message", "Optické dáta nie sú dostupné."),
                    }
                )

            await asyncio.sleep(5)

    except CapabilityNotSupportedError as e:
        await ws.send_json({"status": "not_supported", "error": e.message})
        await ws.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await _send_unexpected_error(ws, e)
    finally:
        if driver:
            driver.close()
