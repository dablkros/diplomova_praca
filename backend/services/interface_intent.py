from __future__ import annotations

import difflib
import ipaddress
import re
from fastapi import HTTPException

def cidr_to_ip_mask(cidr: str) -> tuple[str, str]:
    iface = ipaddress.ip_interface(cidr)
    return str(iface.ip), str(iface.network.netmask)

def nb_to_intended_lines_cisco(nb_iface: dict, nb_ips: list[dict], *, include_admin: bool = True) -> list[str]:
    lines: list[str] = []

    ip4 = next((ip.get("address") for ip in nb_ips if "." in (ip.get("address") or "")), None)
    is_l3 = bool(ip4)

    desc = nb_iface.get("description") or ""
    if desc:
        lines.append(f"description {desc}")

    if include_admin:
        enabled = nb_iface.get("enabled", True)
        lines.append("no shutdown" if enabled else "shutdown")

    if is_l3:
        lines.insert(0, "no switchport")
        ip, mask = cidr_to_ip_mask(ip4)
        lines.append(f"ip address {ip} {mask}")
        return lines

    mode = nb_iface.get("mode")
    untagged = nb_iface.get("untagged_vlan")
    tagged = nb_iface.get("tagged_vlans") or []

    if mode == "access":
        lines.append("switchport")
        lines.append("switchport mode access")
        if untagged and untagged.get("vid"):
            lines.append(f"switchport access vlan {untagged['vid']}")
    elif mode in ("tagged", "tagged-all"):
        lines.append("switchport")
        lines.append("switchport mode trunk")
        if mode == "tagged":
            vids = [str(v["vid"]) for v in tagged if v.get("vid") is not None]
            if vids:
                lines.append(f"switchport trunk allowed vlan {','.join(vids)}")
        if untagged and untagged.get("vid"):
            lines.append(f"switchport trunk native vlan {untagged['vid']}")

    return lines

def nb_to_intended_lines(platform: str, nb_iface: dict, nb_ips: list[dict], *, include_admin: bool = True) -> list[str]:
    p = (platform or "").lower()
    if p in ("ios-xe", "ios"):
        return nb_to_intended_lines_cisco(nb_iface, nb_ips, include_admin=include_admin)
    return []

def normalize_lines(lines: list[str], *, mode: str = "managed") -> list[str]:
    drop_prefixes = ("current configuration", "building configuration")
    drop_exact = {"end", "!"}

    out: list[str] = []
    for ln in lines:
        ln = re.sub(r"\s+", " ", (ln or "").strip())
        if not ln:
            continue
        low = ln.lower()
        if any(low.startswith(p) for p in drop_prefixes):
            continue
        if low in drop_exact:
            continue
        if mode == "managed":
            keep = (
                low.startswith("description ")
                or low == "no switchport"
                or low.startswith("switchport ")
                or low.startswith("ip address ")
            )
            if not keep:
                continue
        out.append(ln)

    return sorted(set(out))

def unified_diff(running_n: list[str], intended_n: list[str]) -> list[str]:
    return list(difflib.unified_diff(
        running_n,
        intended_n,
        fromfile="device_running",
        tofile="sot_intended",
        lineterm=""
    ))
