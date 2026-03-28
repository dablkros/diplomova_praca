from __future__ import annotations

from fastapi import HTTPException

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.core.settings import SSH_USERNAME, SSH_PASSWORD
from backend.drivers import create_driver
from backend.drivers.base import BaseDeviceDriver
from backend.drivers.guards import require_capability


def resolve_creds(username: str | None, password: str | None) -> tuple[str, str]:
    user = (username or "").strip() or (SSH_USERNAME or "")
    pwd = (password or "").strip() or (SSH_PASSWORD or "")
    return user, pwd

def resolve_target(
    netbox: NetBoxClient,
    *,
    device_name: str | None,
    host: str | None,
) -> tuple[str, str]:
    if not device_name and not host:
        raise HTTPException(status_code=400, detail="Chýba device_name aj host")

    if device_name and not host:
        host = netbox.get_device_primary_ip(device_name)

    elif host and not device_name:
        device_name = netbox.get_device_name_by_ip(host)

    else:
        resolved_name = netbox.get_device_name_by_ip(host)
        if resolved_name != device_name:
            raise HTTPException(
                status_code=400,
                detail=f"Host '{host}' nepatrí zariadeniu '{device_name}', ale '{resolved_name}'",
            )

    return device_name, host


def make_driver_from_identity(
    netbox: NetBoxClient,
    *,
    device_name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
) -> tuple[BaseDeviceDriver, str, str]:
    device_name, host = resolve_target(netbox, device_name=device_name, host=host)

    drivers = get_drivers_for_device(netbox, device_name)
    user, pwd = resolve_creds(username, password)

    driver = create_driver(
        platform=drivers["platform"],
        host=host,
        username=user,
        password=pwd,
        netconf_name=drivers["netconf_device_name"],
        netmiko_type=drivers["netmiko_device_type"],
    )
    return driver, host, device_name


def make_driver_with_capability(
    netbox: NetBoxClient,
    *,
    device_name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
    capability_name: str | None = None,
    capability_message: str | None = None,
) -> tuple[BaseDeviceDriver, str, str]:
    driver, resolved_host, resolved_device_name = make_driver_from_identity(
        netbox,
        device_name=device_name,
        host=host,
        username=username,
        password=password,
    )

    if capability_name:
        require_capability(driver, capability_name, capability_message)

    return driver, resolved_host, resolved_device_name