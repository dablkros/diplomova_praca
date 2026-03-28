from __future__ import annotations

from backend.drivers.base import BaseDeviceDriver
from backend.drivers.cisco_iosxe import CiscoIosxeDriver

def create_driver(
    *,
    platform: str,
    host: str,
    username: str,
    password: str,
    netconf_name: str,
    netmiko_type: str,
) -> BaseDeviceDriver:
    slug = (platform or "").strip().lower()

    if slug == "ios-xe":
        return CiscoIosxeDriver(
            host=host,
            username=username,
            password=password,
            netconf_name=netconf_name,
            netmiko_type=netmiko_type,
        )

    return ValueError("Driver not implemented")
