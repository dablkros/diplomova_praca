import asyncio
import difflib
import ipaddress
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import requests
from fastapi.middleware.cors import CORSMiddleware
from netconf_ops import DeviceClient, PortCompareRequest, PortApplyRequest

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

NETBOX_URL = "http://localhost:8000"  # Prípadne uprav podľa reálnej adresy

SSH_USERNAME = os.getenv("SSH_USERNAME")
SSH_PASSWORD = os.getenv("SSH_PASSWORD")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN")

netbox_headers = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json"
}

PLATFORM_MAP = {
    "ios-xe": {"netmiko": "cisco_ios", "netconf": "iosxe"},
    "ios":    {"netmiko": "cisco_ios", "netconf": "ios"},
    "nxos":   {"netmiko": "cisco_nxos", "netconf": "nxos"},
    "junos":  {"netmiko": "juniper_junos", "netconf": "junos"},
    "eos":    {"netmiko": "arista_eos", "netconf": "eos"},
}

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # alebo ["http://localhost:5500"] ak chceš obmedziť
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DeviceInfo(BaseModel):
    device_name: str
    host: str
    interface: str = "vlan 1"
    ip_address: str | None = None
    vendor: str = "cisco"  # predvolene Cisco
    username: str | None = None
    password: str | None = None

def get_device_platform_slug(device_name: str) -> str | None:
    """Vráti platform.slug z NetBoxu pre device_name (alebo None)."""
    r = requests.get(
        f"{NETBOX_URL}/api/dcim/devices/?name={device_name}",
        headers=netbox_headers
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found in NetBox")

    dev = results[0]
    platform = dev.get("platform")
    if not platform:
        return None

    # NetBox typicky vracia {"id":..,"name":..,"slug":..}
    return platform.get("slug") or platform.get("name")


def get_drivers_for_device(device_name: str) -> dict:
    """
    Podľa platformy v SoT vráti dict:
    {"platform": "...", "netmiko_device_type": "...", "netconf_device_name": "..."}
    """
    slug = get_device_platform_slug(device_name)
    if not slug:
        raise HTTPException(
            status_code=400,
            detail=f"Device '{device_name}' nemá v NetBoxe nastavenú platformu (SoT)."
        )

    mapping = PLATFORM_MAP.get(slug)
    if not mapping:
        raise HTTPException(
            status_code=400,
            detail=f"Platform '{slug}' nie je namapovaná v PLATFORM_MAP."
        )

    return {
        "platform": slug,
        "netmiko_device_type": mapping["netmiko"],
        "netconf_device_name": mapping["netconf"],
    }

class ClearMacTableRequest(BaseModel):
    device_name: str
    host: str
    username: str | None = None
    password: str | None = None

    interface: str | None = None
    vlan: int | None = None

    dynamic_only: bool = True


# =====================================================================================
#                             EXISTUJÚCE ENDPOINTY
# =====================================================================================

@app.get("/devices")
def get_devices():
    """
    Vráti zoznam zariadení z NetBoxu:
    - name
    - primary_ip4
    - manufacturer
    - platform
    - model
    """
    try:
        resp = requests.get(f"{NETBOX_URL}/api/dcim/devices/?limit=100", headers=netbox_headers)
        resp.raise_for_status()
        data = resp.json()["results"]

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

@app.get("/interfaces")
def get_interfaces(device_name: str = Query(..., description="Názov zariadenia")):
    """
    Vráti zoznam rozhraní (interfaces) pre zariadenie podľa názvu.
    """
    try:
        # Získaj ID zariadenia podľa názvu
        device_resp = requests.get(
            f"{NETBOX_URL}/api/dcim/devices/?name={device_name}",
            headers=netbox_headers
        )
        device_resp.raise_for_status()
        results = device_resp.json().get("results")

        if not results:
            raise HTTPException(status_code=404, detail="Zariadenie nebolo nájdené")

        device_id = results[0]["id"]

        # Získaj rozhrania tohto zariadenia
        iface_resp = requests.get(
            f"{NETBOX_URL}/api/dcim/interfaces/?device_id={device_id}&limit=100",
            headers=netbox_headers
        )
        iface_resp.raise_for_status()
        interfaces = iface_resp.json().get("results")

        return [
            {
                "name": iface["name"],
                "type": iface["type"]["label"] if iface["type"] else None,
                "description": iface.get("description", ""),
                "mac_address": iface.get("mac_address", ""),
                "enabled": iface.get("enabled", True)
            }
            for iface in interfaces
        ]

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/users")
def get_users():
    """
    Vráti zoznam užívateľov z NetBoxu (príklad, ak používaš plugin NetBox Users).
    """
    try:
        resp = requests.get(f"{NETBOX_URL}/api/users/users/?limit=100", headers=netbox_headers)
        data = resp.json()["results"]
        return [{"username": u["username"]} for u in data]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/check_macaddress")
def over_mac_adresu(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )
    try:
        macs = client.get_mac_table(data.interface)
        return {
            "stav": f"MAC adresy na porte {data.interface}",
            "mac_adresy": macs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/clear-dhcp")
def vycisti_dhcp(data: DeviceInfo):
    if not data.ip_address:
        raise HTTPException(status_code=400, detail="Chýba IP adresa")
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        resp = client.clear_dhcp(data.ip_address)
        return {"stav": f"DHCP lease pre {data.ip_address} uvoľnený", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/show-counters")
def show_interface_counters(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        counters = client.show_counters(data.interface)
        return {
            "stav": f"Packet counters pre {data.interface}",
            "counters": counters
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@app.post("/show-status-int")
def show_interface_status(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )
    try:
        state = client.get_interface_state(data.interface)
        return {
            "stav": f"Stav interface {data.interface}",
            "state": state
        }
    finally:
        client.close()



@app.post("/show-dhcp-bindings")
def show_dhcp_bindings(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        bindings = client.show_dhcp_bindings()
        return {
            "stav": "DHCP bindings",
            "bindings": bindings
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/clear-dhcp-binding")
def clear_dhcp_binding(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        result = client.clear_dhcp_binding(data.ip_address)
        return {
            "stav": f"DHCP binding pre {data.ip_address} bol vymazaný",
            "odpoveď": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/clear-counters")
def reset_counters(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        result = client.clear_counters(data.interface)
        return {
            "stav": f"Countery na porte {data.interface} boli vyresetované",
            "odpoveď": result
        }
    finally:
        client.close()


@app.post("/shutdown")
def vypni_interface(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        resp = client.shutdown(data.interface)
        return {"stav": "interface vypnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/no-shutdown")
def zapni_interface(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        resp = client.no_shutdown(data.interface)
        return {"stav": "interface zapnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/restart-interface")
def restart_iface(data: DeviceInfo):
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        resp1, resp2 = client.restart(data.interface)
        return {
            "stav": f"Interface {data.interface} bol reštartovaný",
            "shutdown": str(resp1), "no_shutdown": str(resp2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


# =====================================================================================
#                             NOVÉ ENDPOINTY (REGION, SUBREGION, SITE, FILTROVANIE)
# =====================================================================================

@app.get("/regions")
def get_regions():
    """
    Vráti iba 'top-level' regióny, ktoré nemajú parent (t. j. parent == None).
    """
    try:
        url = f"{NETBOX_URL}/api/dcim/regions/?limit=100"
        resp = requests.get(url, headers=netbox_headers)
        resp.raise_for_status()
        data = resp.json()["results"]

        # Odfiltrujeme tie, ktoré majú parent == None
        top_regions = [r for r in data if r["parent"] is None]

        return top_regions

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/regions/{region_id}/subregions")
def get_subregions(region_id: int):
    """
    Vráti všetky regióny, ktorých parent je region_id (subregióny).
    """
    try:
        url = f"{NETBOX_URL}/api/dcim/regions/?parent_id={region_id}&limit=100"
        resp = requests.get(url, headers=netbox_headers)
        resp.raise_for_status()
        data = resp.json()
        return data["results"]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sites")
def get_sites(region_id: int | None = None):
    """
    Vráti zoznam site-ov.
    - Ak je zadaný region_id, vráti iba site-y patriace danému regiónu.
    - Inak vráti všetky (limit=100).
    """
    try:
        base_url = f"{NETBOX_URL}/api/dcim/sites/?limit=100"
        if region_id:
            base_url += f"&region_id={region_id}"

        resp = requests.get(base_url, headers=netbox_headers)
        resp.raise_for_status()
        data = resp.json()
        return data["results"]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/devices/filter")
def get_devices_filtered(site_id: int | None = None):
    """
    Vráti zariadenia vyfiltrované podľa site_id (ak je zadané),
    inak vráti všetky zariadenia (limit=100).
    """
    try:
        base_url = f"{NETBOX_URL}/api/dcim/devices/?limit=100"
        if site_id:
            base_url += f"&site_id={site_id}"

        resp = requests.get(base_url, headers=netbox_headers)
        resp.raise_for_status()
        data = resp.json()

        devices_out = []
        for d in data["results"]:
            ip = d.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            site_name = d["site"]["name"] if d.get("site") else None
            devices_out.append({"name": d["name"], "ip": ip_addr, "site": site_name})
        return devices_out

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/devices/by-region")
def get_devices_by_region(region_id: int):
    """
    Vráti zariadenia pre daný región (limit=100).
    - Najprv zistí site-y pre region_id.
    - Potom zavolá devices so zoznamom site_id__in.
    - (V prípade subregiónov by si mohol potrebovať rekurziu.)
    """
    try:
        # Získame site-y pre daný region_id
        base_url = f"{NETBOX_URL}/api/dcim/sites/?region_id={region_id}&limit=100"
        sites_resp = requests.get(base_url, headers=netbox_headers)
        sites_resp.raise_for_status()
        sites_data = sites_resp.json()["results"]
        site_ids = [s["id"] for s in sites_data]

        if not site_ids:
            # Žiadne site-y pre daný región
            return []

        # Pripravíme filter site_id__in
        in_param = ",".join(str(id_) for id_ in site_ids)
        devices_url = f"{NETBOX_URL}/api/dcim/devices/?limit=100&site_id__in={in_param}"

        resp = requests.get(devices_url, headers=netbox_headers)
        resp.raise_for_status()
        devices_data = resp.json()["results"]

        output = []
        for d in devices_data:
            ip = d.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            site_name = d["site"]["name"] if d.get("site") else None
            output.append({
                "name": d["name"],
                "ip": ip_addr,
                "site": site_name
            })
        return output

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Chyba pri volaní NetBox API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def get_nb_interface(device_name: str, interface: str) -> dict:
    """
    Načíta z NetBoxu pre dané device_name a interface:
      - description (str)
      - mode ("access" alebo "trunk")
      - vlan (str; untagged VLAN vid pre access, alebo zoznam tagged VLAN vid oddelených čiarkami)
    """
    # 1) Získaj device ID
    r = requests.get(
        f"{NETBOX_URL}/api/dcim/devices/?name={device_name}",
        headers=netbox_headers
    )
    r.raise_for_status()
    devs = r.json().get("results") or []
    if not devs:
        raise HTTPException(status_code=404, detail=f"Device {device_name} not found")
    dev_id = devs[0]["id"]

    # 2) Získaj interface záznam
    r2 = requests.get(
        f"{NETBOX_URL}/api/dcim/interfaces/?device_id={dev_id}&name={interface}",
        headers=netbox_headers
    )
    r2.raise_for_status()
    ifaces = r2.json().get("results") or []
    if not ifaces:
        raise HTTPException(status_code=404, detail=f"Interface {interface} not found")
    nb = ifaces[0]

    # 3) Extrahuj hodnoty
    description = nb.get("description") or ""
    mode_obj    = nb.get("mode") or {}
    mode        = mode_obj.get("value", "access")

    if mode == "access":
        untag = nb.get("untagged_vlan") or {}
        vid = untag.get("vid")
        vlan = str(vid) if vid is not None else ""
    else:
        tagged = nb.get("tagged_vlans") or []
        # pre trunk zobereme všetky VID
        vlan = ",".join(str(vlan_obj.get("vid")) for vlan_obj in tagged if vlan_obj.get("vid") is not None)

    return {
        "description": description,
        "mode":        mode,
        "vlan":        vlan
    }


@app.post("/configure-interface")
def configure_interface(data: DeviceInfo):
    params = get_nb_interface(data.device_name, data.interface)
    print(f"[DEBUG] NetBox returned for {data.device_name}/{data.interface}: {params!r}")
    drivers = get_drivers_for_device(data.device_name)
    client = DeviceClient(
        data.host,
        SSH_USERNAME,
        SSH_PASSWORD,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        output = client.configure_interface_cli(
            interface=data.interface,
            description=params["description"],
            mode=params["mode"],
            vlan=params["vlan"]
        )
        return {"stav": f"Interface {data.interface} nakonfigurovaný", "output": output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()

@app.websocket("/ws/counters")
async def ws_counters(ws: WebSocket):
    # Prijmeme WebSocket spojenie
    await ws.accept()

    # Očakávame host, username, password, interface v query stringu
    params = ws.query_params
    host      = params.get("host")
    username  = params.get("username")
    password  = params.get("password")
    interface = params.get("interface")
    if not all([host, username, password, interface]):
        await ws.send_json({"error": "Missing parameters"})
        await ws.close()
        return

    # Otvoríme SSH session a DeviceClient
    client = DeviceClient(host, username, password)
    try:
        # Klient vytvorí/pripraví SSH
        conn = client._ensure_ssh()

        # Opakujeme každých 5 sekúnd, kým klient neukončí WS
        while True:
            # získať counters – berieme prvý záznam
            counters = client.show_counters(interface)
            if counters:
                entry = counters[0]
                # poslať JSON s číselnými hodnotami
                await ws.send_json({
                    "input":  int(entry['INPUT_ERRORS']),
                    "output": int(entry['OUTPUT_ERRORS'])
                })
            else:

                await ws.send_json({"error": "No data"})
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        # klient ukončil spojenie
        pass
    except Exception as e:
        # iná chyba
        await ws.send_json({"error": str(e)})
    finally:
        client.close()

@app.websocket("/ws/live-counters")
async def ws_live_counters(ws: WebSocket):
    """
    WebSocket endpoint, ktorý každých 5 s posiela JSON
    { input: <INPUT_PACKETS>, output: <OUTPUT_PACKETS> }
    pre jeden interface, kým klient nezavrie spojenie.
    Query params: host, username, password, interface
    """
    await ws.accept()

    params = ws.query_params
    host      = params.get("host")
    username  = params.get("username")
    password  = params.get("password")
    interface = params.get("interface")
    if not all([host, username, password, interface]):
        await ws.send_json({"error": "Missing parameters"})
        await ws.close()
        return

    client = DeviceClient(host, username, password)
    try:
        # jedna SSH session na celý život WebSocketu
        conn = client._ensure_ssh()

        while True:
            # získať counters pomocou tvojej metódy show_counters()
            counters = client.show_counters(interface)
            if counters:
                entry = counters[0]
                # je to dict, nie objekt
                inp  = int(entry.get("INPUT_PACKETS", 0))
                outp = int(entry.get("OUTPUT_PACKETS", 0))
                await ws.send_json({"input": inp, "output": outp})
            else:
                await ws.send_json({"error": "No data"})
            await asyncio.sleep(5)

    except WebSocketDisconnect:
        # klient ukončil spojenie; ticho končíme
        pass
    except Exception as e:
        await ws.send_json({"error": str(e)})
    finally:
        client.close()

@app.post("/mac-table/clear")
def clear_mac_table(data: ClearMacTableRequest):
    # 1) creds
    user = (data.username or "").strip() or SSH_USERNAME
    pwd = (data.password or "").strip() or SSH_PASSWORD

    # 2) platform + drivers zo SoT (túto funkciu už máš z predchádzajúcich krokov)
    drivers = get_drivers_for_device(data.device_name)
    platform = drivers["platform"]

    # 3) DeviceClient (už multivendor-ready)
    client = DeviceClient(
        data.host,
        user,
        pwd,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )

    try:
        # 4) zavolaj clear
        result = client.clear_mac_table(
            platform=platform,
            interface=data.interface,
            vlan=data.vlan,
            dynamic_only=data.dynamic_only,
        )
        return {
            "device": data.device_name,
            "platform": platform,
            "strategy": "ssh",
            "command": result["command"],
            "output": result["output"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        client.close()

def cidr_to_ip_mask(cidr: str) -> tuple[str, str]:
    iface = ipaddress.ip_interface(cidr)
    return str(iface.ip), str(iface.network.netmask)

def nb_get_interface_detail(device_name: str, interface_name: str) -> dict:
    # 1) nájdi interface podľa device + name
    r = requests.get(
        f"{NETBOX_URL}/api/dcim/interfaces/?device={device_name}&name={interface_name}",
        headers=netbox_headers,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise HTTPException(404, detail=f"Interface '{interface_name}' na device '{device_name}' neexistuje v NetBoxe.")
    iface = results[0]

    # 2) dotiahni IP adresy priradené k interface ID
    iface_id = iface["id"]
    r2 = requests.get(
        f"{NETBOX_URL}/api/ipam/ip-addresses/?interface_id={iface_id}&limit=50",
        headers=netbox_headers,
    )
    r2.raise_for_status()
    ips = r2.json().get("results") or []

    return {"iface": iface, "ips": ips}

#def nb_to_intended_lines_cisco(nb_iface: dict, nb_ips: list[dict]) -> list[str]:
    lines: list[str] = []
    has_ip = any("." in (ip.get("address") or "") for ip in nb_ips)

    if has_ip:
        lines.append("no switchport")

    # description
    desc = nb_iface.get("description") or ""
    if desc:
        lines.append(f"description {desc}")

    # enabled -> shutdown/no shutdown
    enabled = nb_iface.get("enabled", True)
    lines.append("no shutdown" if enabled else "shutdown")

    # mode: NetBox často vracia "access"/"tagged"/"tagged-all"/None podľa verzie
    mode = nb_iface.get("mode")
    untagged = nb_iface.get("untagged_vlan")
    tagged = nb_iface.get("tagged_vlans") or []

    # Ak je mode priamo "access"/"tagged", mapneme na Cisco príkazy
    if mode == "access":
        lines.append("switchport")
        lines.append("switchport mode access")
        if untagged and untagged.get("vid"):
            lines.append(f"switchport access vlan {untagged['vid']}")

    elif mode in ("tagged", "tagged-all"):
        lines.append("switchport")
        lines.append("switchport mode trunk")

        # tagged-all = trunk all -> na Cisco je často default, necháme bez "allowed"
        if mode == "tagged":
            vids = []
            for v in tagged:
                vid = v.get("vid")
                if vid is not None:
                    vids.append(str(vid))
            if vids:
                lines.append(f"switchport trunk allowed vlan {','.join(vids)}")

        # native VLAN z untagged_vlan (ak je)
        if untagged and untagged.get("vid"):
            lines.append(f"switchport trunk native vlan {untagged['vid']}")

    # IP adresy – ak existujú a interface nie je čisto L2
    # NetBox ipam/ip-addresses results majú napr. {"address":"10.0.0.1/24", ...}
    # Vyberieme prvú IPv4 (MVP)
    ip4 = None
    for ip in nb_ips:
        addr = ip.get("address") or ""
        if "." in addr:
            ip4 = addr
            break
    if ip4:
        # Cisco CLI používa masku, nie prefix – v MVP necháme v prefix tvare a len to zobrazíme v diffe
        # (ak chceš, neskôr spravíme prefix->mask konverziu)
        ip, mask = cidr_to_ip_mask(ip4)
        lines.append(f"ip address {ip} {mask}")

    return lines


def nb_to_intended_lines_cisco(nb_iface: dict, nb_ips: list[dict], *, include_admin: bool = True) -> list[str]:
    lines: list[str] = []

    # zisti či máme IPv4
    ip4 = next((ip.get("address") for ip in nb_ips if "." in (ip.get("address") or "")), None)
    is_l3 = bool(ip4)

    # description
    desc = nb_iface.get("description") or ""
    if desc:
        lines.append(f"description {desc}")

    # enabled -> shutdown/no shutdown
        # admin state len ak chceme
        if include_admin:
            enabled = nb_iface.get("enabled", True)
            lines.append("no shutdown" if enabled else "shutdown")

    if is_l3:
        lines.insert(0, "no switchport")
        ip, mask = cidr_to_ip_mask(ip4)
        lines.append(f"ip address {ip} {mask}")
        return lines

    # L2 časť
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

    # ak máš ďalších vendorov:
    # elif p == "junos":
    #     return nb_to_intended_lines_junos(nb_iface, nb_ips, include_admin=include_admin)

    # fallback:
    return []


import re

def normalize_lines(lines: list[str], *, mode: str = "managed") -> list[str]:
    """
    mode:
      - "managed": porovnávaj len riadky, ktoré chceme riadiť zo SoT (MVP)
      - "all": porovnávaj všetko (prísny režim)
    """
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
            # MVP whitelist – riadime iba základné veci
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


class CompareConfigRequest(BaseModel):
    device_name: str
    host: str
    interface: str
    username: str | None = None
    password: str | None = None


@app.post("/interface/compare-config")
def compare_config(data: CompareConfigRequest):
    user = (data.username or "").strip() or SSH_USERNAME
    pwd  = (data.password or "").strip() or SSH_PASSWORD

    drivers = get_drivers_for_device(data.device_name)
    platform = drivers["platform"]

    # 1) SoT intended
    sot = nb_get_interface_detail(data.device_name, data.interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"], include_admin=False)

    # 2) Device running
    client = DeviceClient(
        data.host,
        user,
        pwd,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )
    try:
        running = client.get_running_interface_block(data.interface)
        status = client.get_interface_status_textfsm(data.interface)
    finally:
        client.close()

        # SoT admin state (NetBox)
    sot_enabled = bool(sot["iface"].get("enabled", True))

    # Device admin/oper (ak status vrátil error, nech je None)
    dev_admin_up = status.get("admin_up") if isinstance(status, dict) else None
    dev_oper_up = status.get("oper_up") if isinstance(status, dict) else None

    state_in_sync = (dev_admin_up is None) or (sot_enabled == dev_admin_up)

    # 3) Normalize + diff
    intended_n = normalize_lines(intended, mode="managed")
    running_n = normalize_lines(running, mode="managed")

    diff = list(difflib.unified_diff(
        running_n,
        intended_n,
        fromfile="device_running",
        tofile="sot_intended",
        lineterm=""
    ))

    return {
        "device": data.device_name,
        "platform": platform,
        "interface": data.interface,

        "state": {
            "sot_enabled": sot_enabled,
            "device_admin_up": dev_admin_up,
            "device_oper_up": dev_oper_up,
            "raw": status,
            "in_sync": state_in_sync,
        },

        "config": {
            "in_sync": running_n == intended_n,
            "intended_lines": intended_n,
            "running_lines": running_n,
            "diff": diff,
        },

        # celkové vyhodnotenie:
        "in_sync": (running_n == intended_n) and state_in_sync,
    }


@app.post("/interface/apply-merge")
def apply_interface_merge(data: CompareConfigRequest):  # môžeš použiť ten istý model
    user = (data.username or "").strip() or SSH_USERNAME
    pwd  = (data.password or "").strip() or SSH_PASSWORD

    drivers = get_drivers_for_device(data.device_name)
    platform = drivers["platform"]

    # 1) SoT -> intended lines (rovnako ako compare-config)
    sot = nb_get_interface_detail(data.device_name, data.interface)
    intended = nb_to_intended_lines(platform, sot["iface"], sot["ips"])

    # 2) Push cez SSH (Netmiko)
    client = DeviceClient(
        data.host,
        user,
        pwd,
        netconf_device_name=drivers["netconf_device_name"],
        netmiko_device_type=drivers["netmiko_device_type"],
    )
    try:
        cmds = [f"interface {data.interface}"]

        for line in intended:
            if line.lower().startswith("ip address "):
                cmds.append("no ip address")
            cmds.append(line)

        # ak máš v DeviceClient metódu send_config_lines / send_config_set, použi ju
        output = client.send_config_lines(cmds)
        return {
            "status": "APPLIED_MERGE",
            "device": data.device_name,
            "platform": platform,
            "interface": data.interface,
            "commands": cmds,
            "output": output,
        }
    finally:
        client.close()




