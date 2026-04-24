from __future__ import annotations

import requests
from fastapi import APIRouter, HTTPException, Query

from backend.clients.netbox_client import NetBoxClient

router = APIRouter(tags=["inventory"])
netbox = NetBoxClient()


def _primary_ip(device: dict) -> str:
    ip_data = device.get("primary_ip4") or {}
    return ip_data.get("address", "").split("/")[0] if ip_data else ""


def _device_summary(device: dict) -> dict:
    manufacturer = None
    if device.get("device_type") and device["device_type"].get("manufacturer"):
        manufacturer = device["device_type"]["manufacturer"].get("name")

    platform = None
    if device.get("platform"):
        platform = device["platform"].get("slug") or device["platform"].get("name")

    model = device.get("device_type", {}).get("model") if device.get("device_type") else None

    return {
        "name": device["name"],
        "ip": _primary_ip(device),
        "manufacturer": manufacturer,
        "platform": platform,
        "model": model,
    }


def _compact_device_with_site(device: dict) -> dict:
    site_name = device["site"]["name"] if device.get("site") else None
    return {
        "name": device["name"],
        "ip": _primary_ip(device),
        "site": site_name,
    }


@router.get("/devices")
def get_devices():
    try:
        return [_device_summary(device) for device in netbox.list_devices(limit=100)]
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
        return [region for region in data if region.get("parent") is None]
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
        return [_compact_device_with_site(device) for device in data]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/devices/by-region")
def get_devices_by_region(region_id: int):
    try:
        devices = netbox.list_devices_by_region(region_id=region_id, limit=100)
        return [_compact_device_with_site(device) for device in devices]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
