from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.schemas.common import DeviceInfo, ClearMacTableRequest
from backend.services.device_service import resolve_creds, make_device_client

router = APIRouter(tags=["ops"])
netbox = NetBoxClient()

@router.post("/check_macaddress")
def over_mac_adresu(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        macs = client.get_mac_table(data.interface)
        return {"stav": f"MAC adresy na porte {data.interface}", "mac_adresy": macs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/clear-dhcp")
def vycisti_dhcp(data: DeviceInfo):
    if not data.ip_address:
        raise HTTPException(status_code=400, detail="Chýba IP adresa")
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        resp = client.clear_dhcp(data.ip_address)
        return {"stav": f"DHCP lease pre {data.ip_address} uvoľnený", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/show-counters")
def show_interface_counters(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        counters = client.show_counters(data.interface)
        return {"stav": f"Packet counters pre {data.interface}", "counters": counters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/show-status-int")
def show_interface_status(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        state = client.get_interface_state(data.interface)
        return {"stav": f"Stav interface {data.interface}", "state": state}
    finally:
        client.close()

@router.post("/show-dhcp-bindings")
def show_dhcp_bindings(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        bindings = client.show_dhcp_bindings()
        return {"stav": "DHCP bindings", "bindings": bindings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/clear-dhcp-binding")
def clear_dhcp_binding(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        result = client.clear_dhcp_binding(data.ip_address)
        return {"stav": f"DHCP binding pre {data.ip_address} bol vymazaný", "odpoveď": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/clear-counters")
def reset_counters(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        result = client.clear_counters(data.interface)
        return {"stav": f"Countery na porte {data.interface} boli vyresetované", "odpoveď": result}
    finally:
        client.close()

@router.post("/shutdown")
def vypni_interface(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        resp = client.shutdown(data.interface)
        return {"stav": "interface vypnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/no-shutdown")
def zapni_interface(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        resp = client.no_shutdown(data.interface)
        return {"stav": "interface zapnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/restart-interface")
def restart_iface(data: DeviceInfo):
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        resp1, resp2 = client.restart(data.interface)
        return {"stav": f"Interface {data.interface} bol reštartovaný", "shutdown": str(resp1), "no_shutdown": str(resp2)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/mac-table/clear")
def clear_mac_table(data: ClearMacTableRequest):
    user, pwd = resolve_creds(data.username, data.password)
    drivers = get_drivers_for_device(netbox, data.device_name)
    platform = drivers["platform"]

    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        result = client.clear_mac_table(
            platform=platform,
            interface=data.interface,
            vlan=data.vlan,
            dynamic_only=data.dynamic_only,
        )
        return {
            "device": data.device_name,
            "platform": platform,
            "strategy": "ssh",
            "command": result["command"],
            "output": result["output"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        client.close()
