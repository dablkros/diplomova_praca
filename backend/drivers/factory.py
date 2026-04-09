from __future__ import annotations

from backend.drivers.base import BaseDeviceDriver
from backend.drivers.cisco_iosxe import CiscoIosxeDriver
from backend.drivers.mikrotikrest import MikroTikRestDriver


def create_driver(
    *,
    platform: str,
    host: str,
    username: str,
    password: str,
    netconf_name: str | None = None,
    netmiko_type: str | None = None,
) -> BaseDeviceDriver:
    slug = (platform or "").strip().lower()

    if slug == "ios-xe":
        return CiscoIosxeDriver(
            host=host,
            username=username,
            password=password,
            netconf_name=netconf_name or "iosxe",
            netmiko_type=netmiko_type or "cisco_ios",
        )

    if slug == "mikrotik-routeros":
        return MikroTikRestDriver(
            host=host,
            username=username,
            password=password,
        )

    raise ValueError(f"Driver not implemented for platform '{slug}'")