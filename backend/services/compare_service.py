from __future__ import annotations

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.services.device_service import resolve_creds, make_device_client
from backend.services.interface_intent import nb_to_intended_lines, normalize_lines, unified_diff

def nb_get_interface_detail(netbox: NetBoxClient, device_name: str, interface_name: str) -> dict:
    iface = netbox.get_interface_by_device_and_name(device_name, interface_name)
    ips = netbox.get_interface_ips(iface["id"])
    return {"iface": iface, "ips": ips}

def compare_interface_config(netbox: NetBoxClient, device_name: str, host: str, interface: str, username: str | None, password: str | None) -> dict:
    user, pwd = resolve_creds(username, password)

    drivers = get_drivers_for_device(netbox, device_name)
    platform = drivers["platform"]

    sot = nb_get_interface_detail(netbox, device_name, interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"], include_admin=False)

    client = make_device_client(
        host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        running = client.get_running_interface_block(interface)
        status = client.get_interface_status_textfsm(interface)
    finally:
        client.close()

    sot_enabled = bool(sot["iface"].get("enabled", True))
    dev_admin_up = status.get("admin_up") if isinstance(status, dict) else None
    dev_oper_up = status.get("oper_up") if isinstance(status, dict) else None
    state_in_sync = (dev_admin_up is None) or (sot_enabled == dev_admin_up)

    intended_n = normalize_lines(intended, mode="managed")
    running_n = normalize_lines(running, mode="managed")
    diff = unified_diff(running_n, intended_n)

    return {
        "device": device_name,
        "platform": platform,
        "interface": interface,
        "state": {
            "sot_enabled": sot_enabled,
            "device_admin_up": dev_admin_up,
            "device_oper_up": dev_oper_up,
            "raw": status,
            "in_sync": state_in_sync,
        },
        "config": {
            "in_sync": running_n == intended_n,
            "intended_lines": intended_n,
            "running_lines": running_n,
            "diff": diff,
        },
        "in_sync": (running_n == intended_n) and state_in_sync,
    }

def apply_interface_merge(netbox: NetBoxClient, device_name: str, host: str, interface: str, username: str | None, password: str | None) -> dict:
    user, pwd = resolve_creds(username, password)
    drivers = get_drivers_for_device(netbox, device_name)
    platform = drivers["platform"]

    sot = nb_get_interface_detail(netbox, device_name, interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"])

    client = make_device_client(
        host, user, pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    try:
        cmds = [f"interface {interface}"]
        for line in intended:
            if line.lower().startswith("ip address "):
                cmds.append("no ip address")
            cmds.append(line)
        output = client.send_config_lines(cmds)
        return {
            "status": "APPLIED_MERGE",
            "device": device_name,
            "platform": platform,
            "interface": interface,
            "commands": cmds,
            "output": output,
        }
    finally:
        client.close()
