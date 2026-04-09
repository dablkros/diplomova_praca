from __future__ import annotations

import os
from functools import lru_cache

import requests


def get_macvendors_token() -> str | None:
    return os.getenv("MACVENDORS_TOKEN")


def normalize_mac(mac_address: str) -> str:
    return mac_address.strip().upper().replace("-", ":")


@lru_cache(maxsize=512)
def lookup_mac_vendor(mac_address: str) -> str:
    mac_address = normalize_mac(mac_address)

    url = f"https://api.macvendors.com/v1/lookup/{mac_address}"
    token = get_macvendors_token()

    if not token:
        return "Token missing"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("organization_name", "Not Found")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return "Not Found"
        return "Error"
    except Exception:
        return "Error"