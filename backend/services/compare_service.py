from __future__ import annotations

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.services.device_service import make_driver_from_identity
from backend.services.interface_intent import nb_to_intended_lines, normalize_lines, unified_diff


def nb_get_interface_detail(netbox: NetBoxClient, device_name: str, interface_name: str) -> dict:
    iface = netbox.get_interface_by_device_and_name(device_name, interface_name)
    ips = netbox.get_interface_ips(iface["id"])
    return {"iface": iface, "ips": ips}


def compare_interface_config(
    netbox: NetBoxClient,
    device_name: str,
    host: str,
    interface: str,
    username: str | None,
    password: str | None,
) -> dict:
    driver, resolved_host, resolved_device_name = make_driver_from_identity(
        netbox,
        device_name=device_name,
        host=host,
        username=username,
        password=password,
    )

    drivers = get_drivers_for_device(netbox, resolved_device_name)
    platform = drivers["platform"]

    sot = nb_get_interface_detail(netbox, resolved_device_name, interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"], include_admin=False)

    try:
        running = driver.get_running_interface_block(interface)
        status = driver.get_interface_state(interface)
    finally:
        driver.close()

    sot_enabled = bool(sot["iface"].get("enabled", True))
    if isinstance(status, dict) and status.get("found"):
        link = (status.get("link") or "").lower().strip()
        proto = (status.get("protocol") or "").lower().strip()

        dev_admin_up = link != "administratively down"
        dev_oper_up = proto.startswith("up")

        state_in_sync = (sot_enabled == dev_admin_up)
    else:
        dev_admin_up = None
        dev_oper_up = None
        state_in_sync = True

    intended_n = normalize_lines(intended, mode="managed")
    running_n = normalize_lines(running, mode="managed")
    diff = unified_diff(running_n, intended_n)

    return {
        "device": resolved_device_name,
        "host": resolved_host,
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


def apply_interface_merge(
    netbox: NetBoxClient,
    device_name: str,
    host: str,
    interface: str,
    username: str | None,
    password: str | None,
) -> dict:
    driver, resolved_host, resolved_device_name = make_driver_from_identity(
        netbox,
        device_name=device_name,
        host=host,
        username=username,
        password=password,
    )

    drivers = get_drivers_for_device(netbox, resolved_device_name)
    platform = drivers["platform"]

    sot = nb_get_interface_detail(netbox, resolved_device_name, interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"])

    try:
        cmds = [f"interface {interface}"]
        for line in intended:
            if line.lower().startswith("ip address "):
                cmds.append("no ip address")
            cmds.append(line)

        output = driver.send_config_lines(cmds)

        return {
            "status": "APPLIED_MERGE",
            "device": resolved_device_name,
            "host": resolved_host,
            "platform": platform,
            "interface": interface,
            "commands": cmds,
            "output": output,
        }
    finally:
        driver.close()