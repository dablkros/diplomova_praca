from __future__ import annotations
from pydantic import BaseModel

class DeviceInfo(BaseModel):
    device_name: str
    host: str
    interface: str = "vlan 1"
    ip_address: str | None = None
    vendor: str = "cisco"
    username: str | None = None
    password: str | None = None

class ClearMacTableRequest(BaseModel):
    device_name: str
    host: str
    username: str | None = None
    password: str | None = None
    interface: str | None = None
    vlan: int | None = None
    dynamic_only: bool = True

class CompareConfigRequest(BaseModel):
    device_name: str
    host: str
    interface: str
    username: str | None = None
    password: str | None = None
