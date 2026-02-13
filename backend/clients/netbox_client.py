from __future__ import annotations

import requests
from fastapi import HTTPException

from backend.core.settings import NETBOX_URL, NETBOX_HEADERS

class NetBoxClient:
    def __init__(self, base_url: str = NETBOX_URL, headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or NETBOX_HEADERS

    def _get(self, path: str, **params):
        url = f"{self.base_url}{path}"
        r = requests.get(url, headers=self.headers, params=params or None)
        r.raise_for_status()
        return r.json()

    def get_device_by_name(self, device_name: str) -> dict:
        data = self._get("/api/dcim/devices/", name=device_name)
        results = data.get("results") or []
        if not results:
            raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found in NetBox")
        return results[0]

    def get_device_platform_slug(self, device_name: str) -> str | None:
        dev = self.get_device_by_name(device_name)
        platform = dev.get("platform")
        if not platform:
            return None
        return platform.get("slug") or platform.get("name")

    def list_devices(self, limit: int = 100) -> list[dict]:
        return (self._get("/api/dcim/devices/", limit=limit).get("results") or [])

    def list_interfaces_for_device(self, device_name: str, limit: int = 100) -> list[dict]:
        dev = self.get_device_by_name(device_name)
        device_id = dev["id"]
        data = self._get("/api/dcim/interfaces/", device_id=device_id, limit=limit)
        return (data.get("results") or [])

    def list_users(self, limit: int = 100) -> list[dict]:
        # if you don't use this plugin, you can remove the endpoint/router
        return (self._get("/api/users/users/", limit=limit).get("results") or [])

    def list_regions(self, limit: int = 100) -> list[dict]:
        return (self._get("/api/dcim/regions/", limit=limit).get("results") or [])

    def list_subregions(self, parent_id: int, limit: int = 100) -> list[dict]:
        return (self._get("/api/dcim/regions/", parent_id=parent_id, limit=limit).get("results") or [])

    def list_sites(self, region_id: int | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit}
        if region_id:
            params["region_id"] = region_id
        return (self._get("/api/dcim/sites/", **params).get("results") or [])

    def list_devices_filtered(self, site_id: int | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit}
        if site_id:
            params["site_id"] = site_id
        return (self._get("/api/dcim/devices/", **params).get("results") or [])

    def list_devices_by_region(self, region_id: int, limit: int = 100) -> list[dict]:
        sites = self.list_sites(region_id=region_id, limit=limit)
        site_ids = [s["id"] for s in sites]
        if not site_ids:
            return []
        # NetBox supports site_id__in
        in_param = ",".join(str(i) for i in site_ids)
        data = self._get("/api/dcim/devices/", limit=limit, site_id__in=in_param)
        return (data.get("results") or [])

    def get_interface_by_device_and_name(self, device_name: str, interface_name: str) -> dict:
        data = self._get("/api/dcim/interfaces/", device=device_name, name=interface_name)
        results = data.get("results") or []
        if not results:
            raise HTTPException(404, detail=f"Interface '{interface_name}' na device '{device_name}' neexistuje v NetBoxe.")
        return results[0]

    def get_interface_ips(self, interface_id: int, limit: int = 50) -> list[dict]:
        data = self._get("/api/ipam/ip-addresses/", interface_id=interface_id, limit=limit)
        return (data.get("results") or [])
