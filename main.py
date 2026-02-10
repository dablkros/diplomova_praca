import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import requests
from fastapi.middleware.cors import CORSMiddleware
from netconf_ops import DeviceClient

NETBOX_URL = "http://localhost:8000"  # Prípadne uprav podľa reálnej adresy

load_dotenv()
SSH_USERNAME = os.getenv("SSH_USERNAME")
SSH_PASSWORD = os.getenv("SSH_PASSWORD")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN")

netbox_headers = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json"
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


# =====================================================================================
#                             EXISTUJÚCE ENDPOINTY
# =====================================================================================

@app.get("/devices")
def get_devices():
    """
    Vráti zoznam zariadení (devices) z NetBoxu,
    pričom extrahuje name a primary_ip4 (ak existuje).
    """
    try:
        resp = requests.get(f"{NETBOX_URL}/api/dcim/devices/?limit=100", headers=netbox_headers)
        data = resp.json()["results"]
        result = []
        for device in data:
            ip = device.get("primary_ip4") or {}
            ip_addr = ip.get("address", "").split("/")[0] if ip else ""
            result.append({"name": device["name"], "ip": ip_addr})
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
    try:
        resp = client.clear_dhcp(data.ip_address)
        return {"stav": f"DHCP lease pre {data.ip_address} uvoľnený", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/show-counters")
def show_interface_counters(data: DeviceInfo):
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
    try:
        resp = client.shutdown(data.interface)
        return {"stav": "interface vypnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/no-shutdown")
def zapni_interface(data: DeviceInfo):
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
    try:
        resp = client.no_shutdown(data.interface)
        return {"stav": "interface zapnutý", "odpoveď": str(resp)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        client.close()


@app.post("/restart-interface")
def restart_iface(data: DeviceInfo):
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
    client = DeviceClient(data.host, SSH_USERNAME, SSH_PASSWORD)
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
