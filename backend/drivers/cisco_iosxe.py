from __future__ import annotations

import re
import time
import ipaddress
import textfsm
import xml.etree.ElementTree as ET

from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from ncclient import manager
from ncclient.xml_ import to_ele
from netmiko import ConnectHandler

from backend.drivers.base import BaseDeviceDriver
from backend.drivers.capabilities import DeviceCapabilities
from backend.utils.mac_vendor import lookup_mac_vendor

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def get_template_path(name: str) -> str:
    return str(TEMPLATES_DIR / name)


def render_template(template_name: str, **kwargs) -> str:
    template = env.get_template(template_name)
    return template.render(**kwargs)

def get_interface_type_and_name(interface: str):
    interface = interface.strip().replace(" ", "")
    match = re.match(r"([a-zA-Z-]+)([\d\/\.]+)", interface)
    if not match:
        raise ValueError(f'"{interface}" is not a valid value.')

    raw_type = match.group(1).lower()
    iface_name = match.group(2)

    type_map = {
        "gigabitethernet": "GigabitEthernet",
        "fastethernet": "FastEthernet",
        "tengigabitethernet": "TenGigabitEthernet",
        "twogigabitethernet": "TwoGigabitEthernet",
        "fivegigabitethernet": "FiveGigabitEthernet",
        "twentyfivegige": "TwentyFiveGigE",
        "fortygigabitethernet": "FortyGigabitEthernet",
        "hundredgige": "HundredGigE",
        "port-channel": "Port-channel",
        "loopback": "Loopback",
        "tunnel": "Tunnel",
        "vlan": "Vlan",
    }

    if raw_type not in type_map:
        raise ValueError(f"Typ rozhrania nie je podporovaný: {raw_type}")

    return type_map[raw_type], iface_name


NS = {
    "ifo": "http://cisco.com/ns/yang/Cisco-IOS-XE-interfaces-oper",
}

SPEED_MAP = {
    "speed-10mb": "10Mb/s",
    "speed-100mb": "100Mb/s",
    "speed-1gb": "1000Mb/s",
    "speed-2500mb": "2500Mb/s",
    "speed-5gb": "5000Mb/s",
    "speed-10gb": "10Gb/s",
    "speed-25gb": "25Gb/s",
    "speed-40gb": "40Gb/s",
    "speed-50gb": "50Gb/s",
    "speed-100gb": "100Gb/s",
    "speed-400gb": "400Gb/s",
    "speed-auto": "auto",
    "speed-unknown": "",
}

DUPLEX_MAP = {
    "full-duplex": "full",
    "half-duplex": "half",
    "auto-duplex": "auto",
    "unknown-duplex": "",
}


def _get_text(root, xpath: str):
    node = root.find(xpath, namespaces=NS)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _bps_to_human(speed_bps: int) -> str:
    if speed_bps >= 1_000_000_000:
        return f"{speed_bps // 1_000_000_000}Gb/s"
    if speed_bps >= 1_000_000:
        return f"{speed_bps // 1_000_000}Mb/s"
    if speed_bps >= 1_000:
        return f"{speed_bps // 1_000}Kb/s"
    return f"{speed_bps}b/s"


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
            entry["VENDOR"] = lookup_mac_vendor(mac)

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

    def _find_text(self, parent, path: str, ns: dict, default: str = "") -> str:
        node = parent.find(path, ns)
        return (node.text or "").strip() if node is not None and node.text else default

    def get_interface_state(self, interface: str) -> dict:
        self._require_netconf()

        filter_xml = f"""
        <filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
          <oper-data-format-text-block>
            <exec>show interfaces {interface}</exec>
          </oper-data-format-text-block>
        </filter>
        """

        reply = self.netconf.get(filter=filter_xml)
        raw_xml = reply.xml
        root = ET.fromstring(raw_xml)

        response_text = ""
        for elem in root.iter():
            if elem.tag.endswith("response") and elem.text:
                response_text = elem.text.strip()
                break

        if not response_text:
            return {
                "interface": interface,
                "found": False,
                "port_up": False,
                "link": "down",
                "protocol": "down",
                "admin_status": "",
                "oper_status": "",
                "duplex": "",
                "speed": "",
                "raw_xml": raw_xml,
            }

        first_line = ""
        duplex = ""
        speed = ""

        for line in response_text.splitlines():
            s = line.strip()

            if not first_line and " is " in s and "line protocol is" in s:
                first_line = s

            if "duplex" in s.lower() and "mb/s" in s.lower():
                parts = [p.strip() for p in s.split(",")]
                if len(parts) >= 2:
                    duplex = parts[0].lower().replace("-duplex", "")
                    speed = parts[1]
                break

        oper_up = " is up, line protocol is up" in first_line.lower()

        return {
            "interface": interface,
            "found": True,
            "port_up": oper_up,
            "link": "up" if oper_up else "down",
            "protocol": "up" if oper_up else "down",
            "admin_status": "UP" if oper_up else "",
            "oper_status": "UP" if oper_up else "DOWN" if first_line else "",
            "duplex": duplex,
            "speed": speed,
            "raw_xml": raw_xml,
        }

    def get_interface_counters(self, interface: str) -> list[dict]:
        self._require_netconf()

        ns = {
            "ocif": "http://openconfig.net/yang/interfaces",
        }

        filter_xml = render_template(
            "cisco/openconfig_interface_counters.xml.j2",
            interface=interface,
        )

        reply = self.netconf.get(("subtree", filter_xml))
        raw_xml = reply.data_xml
        root = ET.fromstring(raw_xml)

        iface_node = None
        for node in root.findall(".//ocif:interface", ns):
            name = (
                    self._find_text(node, "ocif:name", ns)
                    or self._find_text(node, "ocif:state/ocif:name", ns)
            )
            if name == interface:
                iface_node = node
                break

        if iface_node is None:
            return []

        counters = iface_node.find("ocif:state/ocif:counters", ns)
        if counters is None:
            return []

        return [{
            "INTERFACE": interface,
            "INPUT_OCTETS": self._find_text(counters, "ocif:in-octets", ns),
            "INPUT_UNICAST_PKTS": self._find_text(counters, "ocif:in-unicast-pkts", ns),
            "INPUT_BROADCAST_PKTS": self._find_text(counters, "ocif:in-broadcast-pkts", ns),
            "INPUT_MULTICAST_PKTS": self._find_text(counters, "ocif:in-multicast-pkts", ns),
            "INPUT_DISCARDS": self._find_text(counters, "ocif:in-discards", ns),
            "INPUT_ERRORS": self._find_text(counters, "ocif:in-errors", ns),
            "INPUT_UNKNOWN_PROTOS": self._find_text(counters, "ocif:in-unknown-protos", ns),
            "OUTPUT_OCTETS": self._find_text(counters, "ocif:out-octets", ns),
            "OUTPUT_UNICAST_PKTS": self._find_text(counters, "ocif:out-unicast-pkts", ns),
            "OUTPUT_BROADCAST_PKTS": self._find_text(counters, "ocif:out-broadcast-pkts", ns),
            "OUTPUT_MULTICAST_PKTS": self._find_text(counters, "ocif:out-multicast-pkts", ns),
            "OUTPUT_DISCARDS": self._find_text(counters, "ocif:out-discards", ns),
            "OUTPUT_ERRORS": self._find_text(counters, "ocif:out-errors", ns),
            "LAST_CLEAR": self._find_text(counters, "ocif:last-clear", ns),
            "RAW_XML": raw_xml,
        }]


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
        self._require_netconf()

        rpc = render_template(
            "cisco/cisco_clear_interface_counters.xml.j2",
            interface=interface,
        )

        reply = self.netconf.dispatch(to_ele(rpc))
        result = self._serialize_reply(reply)

        return {
            "ok": True,
            "operation": "clear-counters",
            "interface": interface,
            "result": result,
        }

    def _edit_interface_admin_state(self, interface: str, *, operation: str | None = None) -> str:
        self._require_netconf()

        iface_type, iface_name = get_interface_type_and_name(interface)
        xml = render_template(
            "cisco/cisco_interface_admin_state.xml.j2",
            iface_type=iface_type,
            iface_name=iface_name,
            operation=operation,
        )
        reply = self.netconf.edit_config(target="running", config=xml)
        return self._serialize_reply(reply)

    def shutdown_interface(self, interface: str) -> dict:
        result = self._edit_interface_admin_state(interface)

        return {
            "ok": True,
            "operation": "shutdown",
            "interface": interface,
            "result": result,
        }

    def no_shutdown_interface(self, interface: str) -> dict:
        result = self._edit_interface_admin_state(interface, operation="remove")

        return {
            "ok": True,
            "operation": "no-shutdown",
            "interface": interface,
            "result": result,
        }

    def restart_interface(self, interface: str) -> dict:
        first = self._edit_interface_admin_state(interface)
        time.sleep(2)
        second = self._edit_interface_admin_state(interface, operation="remove")

        return {
            "ok": True,
            "operation": "restart",
            "interface": interface,
            "shutdown_result": first,
            "no_shutdown_result": second,
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

    def enable_netconf(self) -> dict:
        conn = self._ensure_ssh()

        commands = [
            "netconf-yang",
            "netconf ssh",
        ]

        output = conn.send_config_set(commands, cmd_verify=False)

        time.sleep(2)

        if self.netconf:
            try:
                self.netconf.close_session()
            except Exception:
                pass

        self.netconf = None
        self._try_connect_netconf()

        verify_output = conn.send_command("show running-config | include ^netconf-yang")
        enabled = "netconf-yang" in verify_output

        return {
            "ok": enabled,
            "operation": "enable-netconf",
            "configured": enabled,
            "netconf_session_available": self.netconf is not None,
            "command_output": output,
            "verify_output": verify_output,
            "message": "NETCONF bol povolený." if enabled else "NETCONF sa nepodarilo potvrdiť v running-config."
        }

    def check_netconf_enabled(self) -> dict:
        conn = self._ensure_ssh()

        output = conn.send_command(
            "show running-config | include ^netconf-yang|^netconf ssh"
        )

        has_netconf_yang = "netconf-yang" in output
        has_netconf_ssh = "netconf ssh" in output

        if has_netconf_yang and not self.netconf:
            self._try_connect_netconf()

        return {
            "ok": has_netconf_yang,
            "operation": "check-netconf",
            "netconf_yang_present": has_netconf_yang,
            "netconf_ssh_present": has_netconf_ssh,
            "netconf_session_available": self.netconf is not None,
            "verify_output": output,
            "message": (
                "NETCONF-YANG je povolený."
                if has_netconf_yang
                else "NETCONF-YANG nie je povolený."
            ),
        }

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
