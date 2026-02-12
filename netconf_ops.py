import logging
import os
import time
import re
import xml.etree.ElementTree as ET
import requests
import textfsm
from ncclient.xml_ import to_ele
from ncclient import manager
from netmiko import ConnectHandler
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

env = Environment(loader=FileSystemLoader("templates"))

def get_macvendors_token() -> str | None:
    return os.getenv("MACVENDORS_TOKEN")


def render_template(template_name, **kwargs):
    template = env.get_template(template_name)
    return template.render(**kwargs)


def get_interface_type_and_name(interface: str):
    interface = interface.strip().replace(" ", "")
    match = re.match(r"([a-zA-Z]+)([\d\/\.]+)", interface)
    if not match:
        raise ValueError(f'"{interface}" is not a valid value.')
    raw_type = match.group(1).lower()
    iface_name = match.group(2)
    type_map = {
        "gigabitethernet": "GigabitEthernet",
        "fastethernet": "FastEthernet",
        "tengigabitethernet": "TenGigabitEthernet",
        "vlan": "Vlan"
    }
    if raw_type not in type_map:
        raise ValueError(f"Typ rozhrania nie je podporovaný: {raw_type}")
    return type_map[raw_type], iface_name


def get_mac_vendor(mac_address: str) -> str:
    """
    Vráti názov výrobcu pre danú MAC adresu pomocou macvendors v1 API,
    """
    url = f"https://api.macvendors.com/v1/lookup/{mac_address}"
    token = get_macvendors_token()
    if not token:
        return "Token missing"

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("organization_name", "Not Found")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "Not Found"
        return "Error"
    except Exception:
        return "Error"


class DeviceClient:
    """
    Spravuje NETCONF a SSH (Netmiko) spojenie, a ponúka metódy na bežné operácie.
    """

    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            port: int = 830,
            netconf_device_name: str = "iosxe",
            netmiko_device_type: str = "cisco_ios",
    ):
        self.host = host
        self.username = username
        self.password = password
        self.netconf = None
        self.ssh = None
        self.netconf_device_name = netconf_device_name
        self.netmiko_device_type = netmiko_device_type
        self._connect_netconf(port)

    def _connect_netconf(self, port):
        try:
            self.netconf = manager.connect(
                host=self.host,
                port=port,
                username=self.username,
                password=self.password,
                hostkey_verify=False,
                device_params={'name': self.netconf_device_name}
            )
        except Exception as e:
            raise RuntimeError(f"NETCONF connect failed: {e}")

    def _ensure_ssh(self):
        if not self.ssh:
            self.ssh = ConnectHandler(
                device_type=self.netmiko_device_type,
                host=self.host,
                username=self.username,
                password=self.password,
            )
        return self.ssh

    def get_mac_table_netconf(self, interface: str) -> list:
        """
        Pokúsi sa zobraziť MAC tabuľku cez NETCONF exec RPC + TextFSM.
        """
        rpc = f"""
        <exec xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
            <command>show mac address-table interface {interface}</command>
        </exec>
        """
        try:
            resp = self.netconf.dispatch(to_ele(rpc))
            xml_root = ET.fromstring(resp.xml)
            raw = xml_root.find('.//{http://cisco.com/ns/yang/Cisco-IOS-XE-rpc}result').text
            return self._parse_mac_textfsm(raw)
        except Exception:
            return []

    def get_mac_table_ssh(self, interface: str) -> list:
        """
        Fallback: získa MAC tabuľku cez SSH + TextFSM.
        """
        conn = self._ensure_ssh()
        output = conn.send_command(f"show mac address-table interface {interface}")
        return self._parse_mac_textfsm(output)

    def _parse_mac_textfsm(self, raw_output: str) -> list:
        """
        Parsuje surový CLI výstup cez TextFSM a vráti zoznam slovníkov s plnou tabuľkou.
        """
        template_path = os.path.join("templates", "cisco_ios_show_mac-address-table.textfsm")
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(raw_output)
        results = [dict(zip(fsm.header, row)) for row in parsed]
        # doplnenie vendora
        for e in results:
            mac = e.get("DESTINATION_ADDRESS") or e.get("MAC_ADDRESS")
            e["VENDOR"] = get_mac_vendor(mac)
        return results

    def get_mac_table(self, interface: str) -> list[dict]:
        """
        Vráti priamo zoznam dictov s 'mac' a 'vendor' pre dané rozhranie.
        Skúsi NETCONF exec RPC, ak nič nevráti, použije SSH fallback.
        """
        # 1) Skús NETCONF
        entries = self.get_mac_table_netconf(interface)
        # 2) Ak je to prázdne, fallback na SSH
        if not entries:
            entries = self.get_mac_table_ssh(interface)
        # 3) entries už obsahuje dict so všetkými kľúčmi vrátane 'VENDOR'
        # Prejavíme to do finálneho formátu:
        return [
            {"mac": e.get("DESTINATION_ADDRESS") or e.get("MAC_ADDRESS"),
             "vendor": e.get("VENDOR", "Not Found")}
            for e in entries
        ]

    def show_counters(self, interface: str) -> list[dict]:
        """
        Získa countery (počet packetov in/out) pre dané rozhranie cez SSH + TextFSM.
        """
        conn = self._ensure_ssh()
        # 1) Vola CLI príkaz
        output = conn.send_command(f"show interfaces {interface}")
        # 2) Načítame a použijeme TextFSM šablónu
        template_path = os.path.join(
            "templates", "cisco_ios_show_interfaces.textfsm"
        )
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(output)

        # 3) Zoberieme si priamo dict {INTERFACE, INPUT_PACKETS, OUTPUT_PACKETS}
        results = [dict(zip(fsm.header, row)) for row in parsed]
        return results

    def show_dhcp_bindings(self) -> list[dict]:
        """
        Získa výpis 'show ip dhcp binding' cez SSH + TextFSM.
        """
        conn = self._ensure_ssh()
        output = conn.send_command("show ip dhcp binding")

        tpl = os.path.join("templates", "cisco_ios_show_ip_dhcp_binding.textfsm")
        with open(tpl) as f:
            fsm = textfsm.TextFSM(f)
            parsed = fsm.ParseText(output)

        # header = ['IP_ADDRESS','MAC_ADDRESS','LEASE_EXPIRATION','BINDING_TYPE']
        return [dict(zip(fsm.header, row)) for row in parsed]

    def clear_dhcp_binding(self, ip_address: str) -> str:
        """
        Vymaže (clear) DHCP binding pre zadanú IP adresu cez SSH.
        """
        conn = self._ensure_ssh()
        cmd = f"clear ip dhcp binding {ip_address}"
        print(f"[DEBUG clear_dhcp_binding] Sending: {cmd}")
        output = conn.send_command_timing(cmd)
        print(f"[DEBUG clear_dhcp_binding] Received:\n{output!r}")

        # Ak by sa objavil prompt na potvrdenie, potvrďme ho Enterom
        if "[confirm]" in output or "clear all" in output.lower():
            print("[DEBUG clear_dhcp_binding] Confirming with Enter")
            confirm = conn.send_command_timing("\n")
            print(f"[DEBUG clear_dhcp_binding] After confirm:\n{confirm!r}")
            output += confirm

        return output

    def clear_counters(self, interface: str) -> str:
        """
        Reset countery cez SSH, interaktívne potvrdí [confirm].
        """
        conn = self._ensure_ssh()
        cmd = f"clear counters {interface}"
        output = conn.send_command_timing(cmd)
        # Počkám krátko, nech sa prompt stihne objaviť
        time.sleep(0.5)

        # Potvrdíme [confirm] stlačením Enter
        confirm_output = conn.send_command_timing("\n")
        output += confirm_output
        return output

    def clear_dhcp(self, ip_address: str):
        rpc = f"""
        <clear-dhcp-binding xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-dhcp">
            <address>{ip_address}</address>
        </clear-dhcp-binding>
        """
        return self.netconf.dispatch(rpc)

    def shutdown(self, interface: str):
        t, n = get_interface_type_and_name(interface)
        xml = render_template("shutdown.xml.j2", iface_type=t, iface_name=n)
        return self.netconf.edit_config(target="running", config=xml)

    def no_shutdown(self, interface: str):
        t, n = get_interface_type_and_name(interface)
        xml = render_template("shutdown.xml.j2", iface_type=t, iface_name=n, operation="delete")
        return self.netconf.edit_config(target="running", config=xml)

    def restart(self, interface: str):
        resp1 = self.shutdown(interface)
        time.sleep(5)
        resp2 = self.no_shutdown(interface)
        return resp1, resp2

    def close(self):
        if self.netconf:
            self.netconf.close_session()
        if self.ssh:
            self.ssh.disconnect()

    def configure_interface_cli(self,
                                interface: str,
                                description: str,
                                mode: str,
                                vlan: str) -> str:
        """
        Vygeneruje CLI príkazy pre jeden interface a pošle ich cez SSH.
        """
        # 1) vykreslíme Jinja2 šablónu
        tpl = env.get_template("configure_interface_cli.j2")
        cli = tpl.render(
            interface=interface,
            description=description,
            mode=mode,
            vlan=vlan
        )

        # 2) rozdelíme na riadky a pošleme cez Netmiko
        conn = self._ensure_ssh()
        commands = [line.strip() for line in cli.splitlines() if line.strip()]
        output = conn.send_config_set(commands)
        return output

    def send_command(self, cmd: str) -> str:
        conn = self._ensure_ssh()
        return conn.send_command(cmd)

    def send_config_lines(self, lines: list[str]) -> str:
        conn = self._ensure_ssh()
        # Netmiko: send_config_set vie zobrať list
        return conn.send_config_set(lines)

    def get_running_interface_block(self, interface: str) -> list[str]:
        raw = self.send_command(f"show running-config interface {interface}")
        lines = []
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln or ln == "!":
                continue
            if ln.lower().startswith("interface "):
                continue
            if "building configuration" in ln.lower():
                continue
            lines.append(ln)
        return lines

    def get_interface_primary_ip(self, interface: str) -> str | None:
        """
        Cisco: vráti primárnu IPv4 v CIDR (napr. 192.168.60.1/24) alebo None.
        """
        raw = self.send_command(f"show running-config interface {interface}")
        ip = None
        mask = None
        for ln in raw.splitlines():
            ln = ln.strip()
            if ln.startswith("ip address "):
                parts = ln.split()
                # ip address A.B.C.D M.M.M.M
                if len(parts) >= 4 and parts[2].count(".") == 3 and parts[3].count(".") == 3:
                    ip = parts[2]
                    mask = parts[3]
                    break
        if not ip or not mask:
            return None

        import ipaddress
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
        return f"{ip}/{prefix}"



class PortCompareRequest(BaseModel):
    device_name: str
    host: str
    interface: str
    username: str | None = None
    password: str | None = None


class PortApplyRequest(PortCompareRequest):
    # merge = doplní len chýbajúce príkazy (neodstraňuje navyše veci)
    # replace = “celý port podľa SoT” (odporúčané pri tvojom use-case)
    strategy: str = "replace"  # "merge" | "replace"
