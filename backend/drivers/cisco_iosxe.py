from __future__ import annotations

import os
import re
import time
import ipaddress
import requests
import textfsm
import xml.etree.ElementTree as ET

from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from ncclient import manager
from ncclient.xml_ import to_ele
from netmiko import ConnectHandler

from backend.drivers.base import BaseDeviceDriver
from backend.drivers.capabilities import DeviceCapabilities

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def get_template_path(name: str) -> str:
    return str(TEMPLATES_DIR / name)


def render_template(template_name: str, **kwargs) -> str:
    template = env.get_template(template_name)
    return template.render(**kwargs)


def get_macvendors_token() -> str | None:
    return os.getenv("MACVENDORS_TOKEN")


def get_mac_vendor(mac_address: str) -> str:
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
        "vlan": "Vlan",
    }

    if raw_type not in type_map:
        raise ValueError(f"Typ rozhrania nie je podporovaný: {raw_type}")

    return type_map[raw_type], iface_name


class CiscoIosxeDriver(BaseDeviceDriver):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        netconf_name: str = "iosxe",
        netmiko_type: str = "cisco_ios",
        port: int = 830,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.netconf_device_name = netconf_name
        self.netmiko_device_type = netmiko_type

        self.netconf = None
        self.ssh = None

        self._try_connect_netconf()

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            supports_interface_state=True,
            supports_counters=True,
            supports_mac_table=True,
            supports_dhcp_bindings=True,
            supports_clear_dhcp_binding=True,
            supports_admin_toggle=True,
            supports_restart_interface=True,
            supports_clear_counters=True,
            supports_clear_mac_table=True,
            supports_optics=True,
            supports_config_compare=True,
            supports_config_apply=False,
            supports_netconf=True,
            supports_rest_api=False,
            supports_ssh_cli=True,
        )

    def _try_connect_netconf(self) -> None:
        try:
            self.netconf = manager.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                hostkey_verify=False,
                device_params={"name": self.netconf_device_name},
                timeout=10,
            )
        except Exception:
            self.netconf = None

    def _require_netconf(self):
        if not self.netconf:
            raise RuntimeError(
                "NETCONF session is not available (device may not have NETCONF enabled)."
            )

    def _ensure_ssh(self):
        if not self.ssh:
            self.ssh = ConnectHandler(
                device_type=self.netmiko_device_type,
                host=self.host,
                username=self.username,
                password=self.password,
            )
        return self.ssh

    def _serialize_reply(self, reply) -> str:
        if reply is None:
            return ""
        if hasattr(reply, "xml"):
            return reply.xml
        return str(reply)

    def _pick(self, data: dict, *keys):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        return None

    def _safe_float(self, value):
        try:
            if value in (None, "", "N/A"):
                return None
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    def _parse_mac_textfsm(self, raw_output: str) -> list[dict]:
        template_path = get_template_path("cisco_ios_show_mac-address-table.textfsm")
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(raw_output)

        results = [dict(zip(fsm.header, row)) for row in parsed]

        for entry in results:
            mac = entry.get("DESTINATION_ADDRESS") or entry.get("MAC_ADDRESS")
            entry["VENDOR"] = get_mac_vendor(mac)

        return results

    def _parse_optics_textfsm(self, raw_output: str) -> list[dict]:
        template_path = get_template_path(
            "cisco_ios_show_interfaces_transceiver_detail.textfsm"
        )
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(raw_output)

        return [dict(zip(fsm.header, row)) for row in parsed]

    def get_mac_table_netconf(self, interface: str) -> list[dict]:
        self._require_netconf()

        rpc = f"""
        <exec-command xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
          <cmd>show mac address-table interface {interface}</cmd>
        </exec-command>
        """

        try:
            resp = self.netconf.dispatch(to_ele(rpc))
            xml_root = ET.fromstring(resp.xml)
            result_node = xml_root.find(
                './/{http://cisco.com/ns/yang/Cisco-IOS-XE-rpc}result'
            )
            raw = result_node.text if result_node is not None else ""
            return self._parse_mac_textfsm(raw)
        except Exception:
            return []

    def get_mac_table_ssh(self, interface: str) -> list[dict]:
        conn = self._ensure_ssh()
        output = conn.send_command(f"show mac address-table interface {interface}")
        return self._parse_mac_textfsm(output)

    def get_interface_state(self, interface: str) -> dict:
        conn = self._ensure_ssh()
        raw = conn.send_command(f"show interface {interface}")

        template_path = get_template_path("cisco_ios_show_interfaces.textfsm")
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(raw)

        rows = [dict(zip(fsm.header, row)) for row in parsed]

        if not rows:
            return {
                "interface": interface,
                "found": False,
                "raw": raw,
            }

        row = rows[0]
        link = (self._pick(row, "LINK_STATUS", "LINK", "STATUS") or "").lower()
        protocol = (self._pick(row, "PROTOCOL_STATUS", "PROTOCOL") or "").lower()
        duplex = self._pick(row, "DUPLEX", "DUPLEX_MODE")
        speed = self._pick(row, "SPEED", "BW")

        return {
            "interface": interface,
            "found": True,
            "link": link,
            "protocol": protocol,
            "duplex": duplex,
            "speed": speed,
        }

    def get_interface_counters(self, interface: str) -> list[dict]:
        conn = self._ensure_ssh()
        output = conn.send_command(f"show interfaces {interface}")

        template_path = get_template_path("cisco_ios_show_interfaces.textfsm")
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(output)

        return [dict(zip(fsm.header, row)) for row in parsed]

    def get_mac_table(self, interface: str) -> list[dict]:
        entries = []
        try:
            entries = self.get_mac_table_netconf(interface)
        except Exception:
            entries = []

        if not entries:
            entries = self.get_mac_table_ssh(interface)

        return [
            {
                "mac": e.get("DESTINATION_ADDRESS") or e.get("MAC_ADDRESS"),
                "vendor": e.get("VENDOR", "Not Found"),
            }
            for e in entries
        ]

    def get_dhcp_bindings(self) -> list[dict]:
        conn = self._ensure_ssh()
        output = conn.send_command("show ip dhcp binding")

        template_path = get_template_path("cisco_ios_show_ip_dhcp_binding.textfsm")
        with open(template_path) as tpl:
            fsm = textfsm.TextFSM(tpl)
            parsed = fsm.ParseText(output)

        return [dict(zip(fsm.header, row)) for row in parsed]

    def clear_interface_counters(self, interface: str) -> dict:
        conn = self._ensure_ssh()
        cmd = f"clear counters {interface}"

        output = conn.send_command_timing(cmd)
        time.sleep(0.5)
        confirm_output = conn.send_command_timing("\n")
        output += confirm_output

        return {
            "ok": True,
            "operation": "clear-counters",
            "interface": interface,
            "result": output,
        }

    def shutdown_interface(self, interface: str) -> dict:
        self._require_netconf()

        iface_type, iface_name = get_interface_type_and_name(interface)
        xml = render_template(
            "shutdown.xml.j2",
            iface_type=iface_type,
            iface_name=iface_name,
        )
        reply = self.netconf.edit_config(target="running", config=xml)

        return {
            "ok": True,
            "operation": "shutdown",
            "interface": interface,
            "result": self._serialize_reply(reply),
        }

    def no_shutdown_interface(self, interface: str) -> dict:
        self._require_netconf()

        iface_type, iface_name = get_interface_type_and_name(interface)
        xml = render_template(
            "shutdown.xml.j2",
            iface_type=iface_type,
            iface_name=iface_name,
            operation="remove",
        )
        reply = self.netconf.edit_config(target="running", config=xml)

        return {
            "ok": True,
            "operation": "no-shutdown",
            "interface": interface,
            "result": self._serialize_reply(reply),
        }

    def restart_interface(self, interface: str) -> dict:
        first = self.shutdown_interface(interface)
        time.sleep(5)
        second = self.no_shutdown_interface(interface)

        return {
            "ok": True,
            "operation": "restart",
            "interface": interface,
            "shutdown_result": first["result"],
            "no_shutdown_result": second["result"],
        }

    def clear_mac_table(
        self,
        *,
        platform: str,
        interface: str | None = None,
        vlan: int | None = None,
        dynamic_only: bool = True,
    ) -> dict:
        scopes = sum([1 if interface else 0, 1 if vlan is not None else 0])
        if scopes > 1:
            raise ValueError("Zadaj buď interface alebo vlan, nie oboje.")

        p = (platform or "").strip().lower()

        def cisco_like_cmd():
            base = "clear mac address-table"
            if dynamic_only:
                base += " dynamic"
            if interface:
                return f"{base} interface {interface}"
            if vlan is not None:
                return f"{base} vlan {vlan}"
            return base

        if p not in ("ios", "ios-xe", "nxos", "eos"):
            raise ValueError(f"Nepodporovaná platforma pre clear MAC table: {platform}")

        cmd = cisco_like_cmd()

        conn = self._ensure_ssh()
        out = conn.send_command_timing(cmd)

        if "[confirm]" in out.lower() or "confirm" in out.lower():
            out2 = conn.send_command_timing("\n")
            out += out2

        return {
            "ok": True,
            "operation": "clear-mac-table",
            "platform": platform,
            "interface": interface,
            "vlan": vlan,
            "dynamic_only": dynamic_only,
            "command": cmd,
            "result": out,
        }

    def clear_dhcp_binding(self, ip_address: str) -> dict:
        conn = self._ensure_ssh()
        cmd = f"clear ip dhcp binding {ip_address}"

        output = conn.send_command_timing(cmd)

        if "[confirm]" in output.lower() or "clear all" in output.lower():
            confirm = conn.send_command_timing("\n")
            output += confirm

        return {
            "ok": True,
            "operation": "clear-dhcp-binding",
            "ip_address": ip_address,
            "command": cmd,
            "result": output,
        }

    def get_running_interface_block(self, interface: str) -> list[str]:
        conn = self._ensure_ssh()
        raw = conn.send_command(f"show running-config interface {interface}")

        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line == "!":
                continue
            if line.lower().startswith("interface "):
                continue
            if "building configuration" in line.lower():
                continue
            lines.append(line)

        return lines

    def get_interface_primary_ip(self, interface: str) -> str | None:
        conn = self._ensure_ssh()
        raw = conn.send_command(f"show running-config interface {interface}")

        ip = None
        mask = None

        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("ip address "):
                parts = line.split()
                if len(parts) >= 4 and parts[2].count(".") == 3 and parts[3].count(".") == 3:
                    ip = parts[2]
                    mask = parts[3]
                    break

        if not ip or not mask:
            return None

        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
        return f"{ip}/{prefix}"

    def show_optics(self, interface: str) -> dict:
        conn = self._ensure_ssh()

        commands = [
            f"show interfaces {interface} transceiver detail",
            f"show interfaces transceiver detail | section {interface}",
        ]

        raw_output = None
        used_command = None

        for cmd in commands:
            try:
                output = conn.send_command(cmd, read_timeout=30)
                if output and "% Invalid input" not in output and "% Incomplete command" not in output:
                    raw_output = output
                    used_command = cmd
                    break
            except Exception:
                continue

        if not raw_output:
            return {
                "interface": interface,
                "found": False,
                "command_used": None,
                "message": "Nepodarilo sa získať optické údaje zo zariadenia.",
                "raw": "",
            }

        parsed = self._parse_optics_textfsm(raw_output)
        if not parsed:
            return {
                "interface": interface,
                "found": False,
                "command_used": used_command,
                "message": "Výstup sa nepodarilo vyparsovať cez TextFSM.",
                "raw": raw_output,
            }

        row = parsed[0]
        return {
            "interface": row.get("INTERFACE", interface),
            "temperature": self._safe_float(row.get("TEMPERATURE")),
            "voltage": self._safe_float(row.get("VOLTAGE")),
            "tx_power": self._safe_float(row.get("TX_POWER")),
            "rx_power": self._safe_float(row.get("RX_POWER")),
            "tx_bias": self._safe_float(row.get("TX_BIAS")),
            "found": True,
            "command_used": used_command,
            "raw": raw_output,
        }

    def send_config_lines(self, lines: list[str]) -> str:
        conn = self._ensure_ssh()
        commands = [line.strip() for line in lines if line and line.strip()]
        return conn.send_config_set(commands)

    def close(self) -> None:
        if self.netconf:
            try:
                self.netconf.close_session()
            except Exception:
                pass

        if self.ssh:
            try:
                self.ssh.disconnect()
            except Exception:
                pass