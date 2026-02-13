from __future__ import annotations

from backend.core.settings import SSH_USERNAME, SSH_PASSWORD
from backend.netconf_ops import DeviceClient  # keep your existing implementation

def resolve_creds(username: str | None, password: str | None) -> tuple[str, str]:
    user = (username or "").strip() or (SSH_USERNAME or "")
    pwd  = (password or "").strip() or (SSH_PASSWORD or "")
    return user, pwd

def make_device_client(host: str, user: str, pwd: str, *, netconf_name: str, netmiko_type: str) -> DeviceClient:
    return DeviceClient(
        host,
        user,
        pwd,
        netconf_device_name=netconf_name,
        netmiko_device_type=netmiko_type,
    )
