from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.schemas.common import DeviceInfo, CompareConfigRequest
from backend.services.device_service import resolve_creds, make_device_client
from backend.services.compare_service import compare_interface_config, apply_interface_merge

router = APIRouter(tags=["interface"])
netbox = NetBoxClient()

def get_nb_interface(netbox: NetBoxClient, device_name: str, interface: str) -> dict:
    nb = netbox.get_interface_by_device_and_name(device_name, interface)
    description = nb.get("description") or ""
    mode_obj = nb.get("mode") or {}
    mode = mode_obj.get("value", "access") if isinstance(mode_obj, dict) else (mode_obj or "access")

    if mode == "access":
        untag = nb.get("untagged_vlan") or {}
        vid = untag.get("vid")
        vlan = str(vid) if vid is not None else ""
    else:
        tagged = nb.get("tagged_vlans") or []
        vlan = ",".join(str(v.get("vid")) for v in tagged if v.get("vid") is not None)

    return {"description": description, "mode": mode, "vlan": vlan}

@router.post("/configure-interface")
def configure_interface(data: DeviceInfo):
    params = get_nb_interface(netbox, data.device_name, data.interface)
    drivers = get_drivers_for_device(netbox, data.device_name)
    user, pwd = resolve_creds(data.username, data.password)
    client = make_device_client(
        data.host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        output = client.configure_interface_cli(
            interface=data.interface,
            description=params["description"],
            mode=params["mode"],
            vlan=params["vlan"]
        )
        return {"stav": f"Interface {data.interface} nakonfigurovaný", "output": output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@router.post("/interface/compare-config")
def compare_config(data: CompareConfigRequest):
    return compare_interface_config(netbox, data.device_name, data.host, data.interface, data.username, data.password)

@router.post("/interface/apply-merge")
def apply_merge(data: CompareConfigRequest):
    return apply_interface_merge(netbox, data.device_name, data.host, data.interface, data.username, data.password)
