import ipaddress

import requests
from fastapi import HTTPException

from backend.core.settings import NETBOX_HEADERS, NETBOX_URL


class NetBoxClient:
    def __init__(self, base_url: str = NETBOX_URL, headers: dict | None = None, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or NETBOX_HEADERS
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get(self, path: str, **params):
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params or None, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

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

    def get_device_by_ip(self, ip_address: str) -> dict:
        try:
            wanted_ip = str(ipaddress.ip_address(ip_address))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Neplatná IP adresa: {ip_address}") from exc

        data = self._get("/api/ipam/ip-addresses/", address=wanted_ip)
        results = data.get("results") or []

        for entry in results:
            addr = entry.get("address") or ""
            try:
                found_ip = str(ipaddress.ip_interface(addr).ip)
            except ValueError:
                continue

            if found_ip != wanted_ip:
                continue

            assigned = entry.get("assigned_object") or {}
            device = assigned.get("device")

            if device and device.get("name"):
                return self.get_device_by_name(device["name"])

        devices = self.list_devices(limit=500)
        for dev in devices:
            for key in ("primary_ip4", "primary_ip6", "primary_ip"):
                ip_obj = dev.get(key)
                if not ip_obj:
                    continue

                addr = ip_obj.get("address") if isinstance(ip_obj, dict) else None
                if not addr:
                    continue

                try:
                    found_ip = str(ipaddress.ip_interface(addr).ip)
                except ValueError:
                    continue

                if found_ip == wanted_ip:
                    return dev

        raise HTTPException(status_code=404, detail=f"Device with IP '{ip_address}' not found in NetBox")

    def get_device_name_by_ip(self, ip_address: str) -> str:
        dev = self.get_device_by_ip(ip_address)
        name = dev.get("name")
        if not name:
            raise HTTPException(status_code=404, detail=f"Device name for IP '{ip_address}' not found")
        return name

    def list_devices(self, limit: int = 100) -> list[dict]:
        return self._get("/api/dcim/devices/", limit=limit).get("results") or []

    def list_interfaces_for_device(self, device_name: str, limit: int = 100) -> list[dict]:
        dev = self.get_device_by_name(device_name)
        data = self._get("/api/dcim/interfaces/", device_id=dev["id"], limit=limit)
        return data.get("results") or []

    def list_users(self, limit: int = 100) -> list[dict]:
        return self._get("/api/users/users/", limit=limit).get("results") or []

    def list_regions(self, limit: int = 100) -> list[dict]:
        return self._get("/api/dcim/regions/", limit=limit).get("results") or []

    def list_subregions(self, parent_id: int, limit: int = 100) -> list[dict]:
        return self._get("/api/dcim/regions/", parent_id=parent_id, limit=limit).get("results") or []

    def list_sites(self, region_id: int | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit}
        if region_id:
            params["region_id"] = region_id
        return self._get("/api/dcim/sites/", **params).get("results") or []

    def list_devices_filtered(self, site_id: int | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit}
        if site_id:
            params["site_id"] = site_id
        return self._get("/api/dcim/devices/", **params).get("results") or []

    def list_devices_by_region(self, region_id: int, limit: int = 100) -> list[dict]:
        sites = self.list_sites(region_id=region_id, limit=limit)
        site_ids = [site["id"] for site in sites]
        if not site_ids:
            return []

        in_param = ",".join(str(site_id) for site_id in site_ids)
        data = self._get("/api/dcim/devices/", limit=limit, site_id__in=in_param)
        return data.get("results") or []

    def get_interface_by_device_and_name(self, device_name: str, interface_name: str) -> dict:
        data = self._get("/api/dcim/interfaces/", device=device_name, name=interface_name)
        results = data.get("results") or []
        if not results:
            raise HTTPException(404, detail=f"Interface '{interface_name}' na device '{device_name}' neexistuje v NetBoxe.")
        return results[0]

    def get_interface_ips(self, interface_id: int, limit: int = 50) -> list[dict]:
        data = self._get("/api/ipam/ip-addresses/", interface_id=interface_id, limit=limit)
        return data.get("results") or []

    def get_device_primary_ip(self, device_name: str) -> str:
        dev = self.get_device_by_name(device_name)

        for key in ("primary_ip4", "primary_ip6", "primary_ip"):
            ip_obj = dev.get(key)
            if not ip_obj:
                continue

            addr = ip_obj.get("address") if isinstance(ip_obj, dict) else None
            if addr:
                return str(addr).split("/")[0]

        raise HTTPException(
            status_code=404,
            detail=f"Primary IP for device '{device_name}' not found in NetBox",
        )
