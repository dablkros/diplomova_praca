from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
import requests

from backend.clients.netbox_client import NetBoxClient

router = APIRouter(tags=["inventory"])
netbox = NetBoxClient()

@router.get("/devices")
def get_devices():
    try:
        data = netbox.list_devices(limit=100)
        result = []
        for device in data:
            ip = device.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            manufacturer = None
            if device.get("device_type") and device["device_type"].get("manufacturer"):
                manufacturer = device["device_type"]["manufacturer"].get("name")
            platform = None
            if device.get("platform"):
                platform = device["platform"].get("slug") or device["platform"].get("name")
            model = None
            if device.get("device_type"):
                model = device["device_type"].get("model")
            result.append({
                "name": device["name"],
                "ip": ip_addr,
                "manufacturer": manufacturer,
                "platform": platform,
                "model": model,
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/interfaces")
def get_interfaces(device_name: str = Query(..., description="Názov zariadenia")):
    try:
        interfaces = netbox.list_interfaces_for_device(device_name, limit=100)
        return [
            {
                "name": iface["name"],
                "type": iface["type"]["label"] if iface.get("type") else None,
                "description": iface.get("description", ""),
                "mac_address": iface.get("mac_address", ""),
                "enabled": iface.get("enabled", True),
            }
            for iface in interfaces
        ]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users")
def get_users():
    try:
        users = netbox.list_users(limit=100)
        return [{"username": u.get("username")} for u in users]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/regions")
def get_regions():
    try:
        data = netbox.list_regions(limit=100)
        return [r for r in data if r.get("parent") is None]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/regions/{region_id}/subregions")
def get_subregions(region_id: int):
    try:
        return netbox.list_subregions(parent_id=region_id, limit=100)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sites")
def get_sites(region_id: int | None = None):
    try:
        return netbox.list_sites(region_id=region_id, limit=100)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/devices/filter")
def get_devices_filtered(site_id: int | None = None):
    try:
        data = netbox.list_devices_filtered(site_id=site_id, limit=100)
        devices_out = []
        for d in data:
            ip = d.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            site_name = d["site"]["name"] if d.get("site") else None
            devices_out.append({"name": d["name"], "ip": ip_addr, "site": site_name})
        return devices_out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/devices/by-region")
def get_devices_by_region(region_id: int):
    try:
        devices = netbox.list_devices_by_region(region_id=region_id, limit=100)
        output = []
        for d in devices:
            ip = d.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            site_name = d["site"]["name"] if d.get("site") else None
            output.append({"name": d["name"], "ip": ip_addr, "site": site_name})
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
