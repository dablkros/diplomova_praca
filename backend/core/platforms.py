from __future__ import annotations

from fastapi import HTTPException
from backend.clients.netbox_client import NetBoxClient

PLATFORM_MAP = {
    "ios-xe": {"netmiko": "cisco_ios", "netconf": "iosxe"},
    "ios":    {"netmiko": "cisco_ios", "netconf": "ios"},
    "nxos":   {"netmiko": "cisco_nxos", "netconf": "nxos"},
    "junos":  {"netmiko": "juniper_junos", "netconf": "junos"},
    "eos":    {"netmiko": "arista_eos", "netconf": "eos"},
}

def get_drivers_for_device(netbox: NetBoxClient, device_name: str) -> dict:
    slug = netbox.get_device_platform_slug(device_name)
    if not slug:
        raise HTTPException(
            status_code=400,
            detail=f"Device '{device_name}' nemá v NetBoxe nastavenú platformu (SoT)."
        )
    mapping = PLATFORM_MAP.get(slug)
    if not mapping:
        raise HTTPException(
            status_code=400,
            detail=f"Platform '{slug}' nie je namapovaná v PLATFORM_MAP."
        )
    return {
        "platform": slug,
        "netmiko_device_type": mapping["netmiko"],
        "netconf_device_name": mapping["netconf"],
    }
