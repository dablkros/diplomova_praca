from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.clients.netbox_client import NetBoxClient
from backend.schemas.common import DeviceInfo, CompareConfigRequest
from backend.services.device_service import make_driver_from_identity
from backend.services.compare_service import compare_interface_config, apply_interface_merge
from backend.drivers.guards import require_capability
from backend.drivers.errors import CapabilityNotSupportedError

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


def build_interface_commands(interface: str, description: str, mode: str, vlan: str) -> list[str]:
    cmds = [f"interface {interface}"]

    if description:
        cmds.append(f"description {description}")
    else:
        cmds.append("no description")

    if mode == "access":
        cmds.append("switchport mode access")
        if vlan:
            cmds.append(f"switchport access vlan {vlan}")
    else:
        cmds.append("switchport mode trunk")
        if vlan:
            cmds.append(f"switchport trunk allowed vlan {vlan}")

    return cmds


@router.post("/configure-interface")
def configure_interface(data: DeviceInfo):
    params = get_nb_interface(netbox, data.device_name, data.interface)

    driver, _, _ = make_driver_from_identity(
        netbox,
        device_name=data.device_name,
        host=data.host,
        username=data.username,
        password=data.password,
    )

    try:
        require_capability(
            driver,
            "supports_config_apply",
            "Toto zariadenie nepodporuje aplikovanie konfigurácie.",
        )

        cmds = build_interface_commands(
            interface=data.interface,
            description=params["description"],
            mode=params["mode"],
            vlan=params["vlan"],
        )

        output = driver.send_config_lines(cmds)

        return {
            "stav": f"Interface {data.interface} nakonfigurovaný",
            "commands": cmds,
            "output": output,
        }

    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/interface/compare-config")
def compare_config(data: CompareConfigRequest):
    return compare_interface_config(
        netbox,
        data.device_name,
        data.host,
        data.interface,
        data.username,
        data.password,
    )


@router.post("/interface/apply-merge")
def apply_merge(data: CompareConfigRequest):
    return apply_interface_merge(
        netbox,
        data.device_name,
        data.host,
        data.interface,
        data.username,
        data.password,
    )