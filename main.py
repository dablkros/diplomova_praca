import asyncio
import ipaddress
import os
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
        counters = client.show_counters(data.interface)
        return {
            "stav": f"Packet counters pre {data.interface}",
            "counters": counters
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
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

@app.post("/interface/compare-attrs")
def compare_interface_attrs(data: PortCompareRequest):
    user = data.username or SSH_USERNAME
    pwd  = data.password or SSH_PASSWORD

    nb_iface = get_nb_interface_json(data.device_name, data.interface)
    nb_ip = get_nb_primary_ip_cidr(nb_iface["id"])

    client = DeviceClient(data.host, user, pwd)
    try:
        # Device state (minimálne)
        running_lines = normalize_cfg_lines(client.get_running_interface_block(data.interface))
        dev_ip = client.get_interface_primary_ip(data.interface)

        # derived device flags
        dev_has_no_switchport = any(ln.strip().lower() == "no switchport" for ln in running_lines)
        dev_desc = ""
        for ln in running_lines:
            if ln.lower().startswith("description "):
                dev_desc = ln[len("description "):].strip()
                break
        dev_shutdown = any(ln.strip().lower() == "shutdown" for ln in running_lines)

        # NB state
        nb_enabled = bool(nb_iface.get("enabled", True))
        nb_desc = nb_iface.get("description") or ""
        nb_has_ip = nb_ip is not None
        nb_is_l3 = nb_has_ip  # pravidlo: ak je IP v SoT -> L3

        # compare table
        rows = [
            {
                "field": "description",
                "sot": nb_desc,
                "device": dev_desc,
                "match": (nb_desc == dev_desc),
            },
            {
                "field": "enabled",
                "sot": nb_enabled,
                "device": (not dev_shutdown),
                "match": (nb_enabled == (not dev_shutdown)),
            },
            {
                "field": "l3_mode",
                "sot": nb_is_l3,
                "device": dev_has_no_switchport,
                "match": (nb_is_l3 == dev_has_no_switchport),
            },
            {
                "field": "primary_ipv4",
                "sot": nb_ip,
                "device": dev_ip,
                "match": (nb_ip == dev_ip),
            },
        ]

        in_sync = all(r["match"] for r in rows)

        return {
            "device": data.device_name,
            "host": data.host,
            "interface": data.interface,
            "in_sync": in_sync,
            "rows": rows,
            "nb_interface_id": nb_iface["id"],
        }
    finally:
        client.close()


@app.post("/interface/apply-replace")
def apply_interface_replace(data: PortApplyRequest):
    user = data.username or SSH_USERNAME
    pwd  = data.password or SSH_PASSWORD

    nb_iface = get_nb_interface_json(data.device_name, data.interface)
    nb_ip = get_nb_primary_ip_cidr(nb_iface["id"])

    intended_lines = nb_to_cisco_intended_lines(nb_iface, nb_ip)

    client = DeviceClient(data.host, user, pwd)
    try:
        cmds = [
            f"default interface {data.interface}",
            f"interface {data.interface}",
            *intended_lines,
            "end"
        ]
        output = client.send_config_lines(cmds)

        return {
            "stav": "APPLIED",
            "interface": data.interface,
            "intended": intended_lines,
            "output": output
        }
    finally:
        client.close()


def normalize_cfg_lines(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln == "!":
            continue
        if "building configuration" in ln.lower():
            continue
        out.append(ln)
    return out


def get_nb_interface_json(device_name: str, interface: str) -> dict:
    r = requests.get(f"{NETBOX_URL}/api/dcim/devices/?name={device_name}", headers=netbox_headers)
    r.raise_for_status()
    devs = r.json().get("results") or []
    if not devs:
        raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found")
    dev_id = devs[0]["id"]

    r2 = requests.get(
        f"{NETBOX_URL}/api/dcim/interfaces/?device_id={dev_id}&name={interface}",
        headers=netbox_headers
    )
    r2.raise_for_status()
    ifaces = r2.json().get("results") or []
    if not ifaces:
        raise HTTPException(status_code=404, detail=f"Interface '{interface}' not found on '{device_name}'")
    return ifaces[0]


def get_nb_primary_ip_cidr(interface_id: int) -> str | None:
    """
    Vráti prvú IPv4 priradenú k interface v CIDR (napr 192.168.60.1/24) alebo None.
    """
    r = requests.get(
        f"{NETBOX_URL}/api/ipam/ip-addresses/?interface_id={interface_id}&limit=50",
        headers=netbox_headers
    )
    r.raise_for_status()
    ips = r.json().get("results") or []
    # zober prvú IPv4
    for ipobj in ips:
        addr = ipobj.get("address")
        if not addr:
            continue
        try:
            ipaddress.IPv4Interface(addr)
            return addr
        except Exception:
            continue
    return None


def cidr_to_ip_mask(cidr: str) -> tuple[str, str]:
    iface = ipaddress.ip_interface(cidr)
    return str(iface.ip), str(iface.network.netmask)


def nb_to_cisco_intended_lines(nb_iface: dict, nb_ip_cidr: str | None) -> list[str]:
    """
    Cisco intended config z NB (len základ): description, L2/L3 podľa IP, vlan/mode, shutdown/no shut.
    """
    intended = []

    desc = nb_iface.get("description") or ""
    if desc:
        intended.append(f"description {desc}")

    enabled = bool(nb_iface.get("enabled", True))

    # L3 ak existuje IP v SoT, inak L2 podľa mode
    if nb_ip_cidr:
        intended.append("no switchport")
        ip, mask = cidr_to_ip_mask(nb_ip_cidr)
        intended.append(f"ip address {ip} {mask}")
    else:
        mode = (nb_iface.get("mode") or {}).get("value")  # access/tagged/None
        if mode == "access":
            intended.append("switchport mode access")
            untag = nb_iface.get("untagged_vlan")
            if isinstance(untag, dict) and untag.get("vid") is not None:
                intended.append(f"switchport access vlan {untag['vid']}")
        elif mode == "tagged":
            intended.append("switchport mode trunk")
            tagged = nb_iface.get("tagged_vlans") or []
            vids = [v.get("vid") for v in tagged if isinstance(v, dict) and v.get("vid") is not None]
            if vids:
                intended.append(f"switchport trunk allowed vlan {','.join(str(v) for v in vids)}")

    intended.append("no shutdown" if enabled else "shutdown")
    return intended

