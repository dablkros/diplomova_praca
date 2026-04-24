from __future__ import annotations

from typing import Any, Optional
import requests

from backend.drivers.base import BaseDeviceDriver
from backend.drivers.capabilities import DeviceCapabilities
from backend.utils.mac_vendor import lookup_mac_vendor

class MikroTikRestDriver(BaseDeviceDriver):
    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            *,
            port: int = 80,
            use_https: bool = False,
            verify_ssl: bool = False,
            timeout: int = 20,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_https = use_https
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        scheme = "https" if use_https else "http"
        self.base_url = f"{scheme}://{host}:{port}/rest"

        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = verify_ssl
        self.session.headers.update({
            "Content-Type": "application/json"
        })

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
            supports_clear_counters=False,
            supports_clear_mac_table=False,
            supports_optics=False,
            supports_config_compare=False,
            supports_config_apply=False,
            supports_netconf=False,
            supports_rest_api=True,
            supports_ssh_cli=False,
        )

    def _request(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.session.request(
            method=method,
            url=url,
            json=json,
            timeout=self.timeout,
        )
        response.raise_for_status()

        if not response.text.strip():
            return {}

        return response.json()

    def get_system_resource(self) -> Any:
        return self._request("GET", "system/resource")

    def get_interfaces(self) -> Any:
        return self._request("GET", "interface")

    def get_interface(self, name: str) -> dict | None:
        interfaces = self.get_interfaces()
        for iface in interfaces:
            if iface.get("name") == name:
                return iface
        return None

    def _as_bool(self, value) -> bool:
        return str(value).strip().lower() in ("true", "yes", "1")

    def _get_ethernet_monitor(self, interface: str) -> dict | None:
        candidates = [
            ("POST", "interface/ethernet/monitor", {"numbers": interface, "once": "true"}),
            ("POST", "interface/ethernet/monitor", {".id": interface, "once": "true"}),
            ("POST", "interface/ethernet/monitor", {"interface": interface, "once": "true"}),
        ]

        for method, path, payload in candidates:
            try:
                result = self._request(method, path, json=payload)

                if isinstance(result, list) and result:
                    return result[0]

                if isinstance(result, dict) and result:
                    return result
            except Exception:
                continue

        return None

    def get_interface_state(self, interface: str) -> dict:
        iface = self.get_interface(interface)

        if not iface:
            return {
                "interface": interface,
                "found": False,
                "raw": None,
            }

        disabled = self._as_bool(iface.get("disabled"))
        running = self._as_bool(iface.get("running"))

        link = "up" if running else "down"
        protocol = "down" if disabled else ("up" if running else "down")

        duplex = None
        speed = None
        monitor_raw = None

        iface_type = str(iface.get("type", "")).lower()

        if iface_type == "ether":
            monitor_raw = self._get_ethernet_monitor(interface)

            if monitor_raw:
                status = str(monitor_raw.get("status", "")).strip().lower()
                rate = monitor_raw.get("rate")
                full_duplex = monitor_raw.get("full-duplex")

                if status in ("link-ok", "up"):
                    link = "up"
                    if not disabled:
                        protocol = "up"
                elif status in ("no-link", "down"):
                    link = "down"
                    if not disabled:
                        protocol = "down"

                if rate not in (None, "", "unknown"):
                    speed = rate

                if full_duplex is not None:
                    duplex = "full" if self._as_bool(full_duplex) else "half"

        return {
            "interface": interface,
            "found": True,
            "link": link,
            "protocol": protocol,
            "duplex": duplex,
            "speed": speed,
            "enabled": not disabled,
            "running": running,
            "raw": iface,
            "monitor_raw": monitor_raw,
        }

    def set_interface_disabled(self, interface: str, disabled: bool) -> dict:
        iface = self.get_interface(interface)
        if not iface:
            raise ValueError(f"Interface '{interface}' not found")

        iface_id = iface.get(".id")
        if not iface_id:
            raise ValueError(f"Interface '{interface}' has no .id in REST response")

        result = self._request(
            "PATCH",
            f"interface/{iface_id}",
            json={"disabled": "true" if disabled else "false"},
        )

        verify = self.get_interface(interface)

        return {
            "ok": True,
            "operation": "shutdown" if disabled else "no-shutdown",
            "interface": interface,
            "result": result,
            "verify": verify,
        }

    def shutdown_interface(self, interface: str) -> dict:
        return self.set_interface_disabled(interface, True)

    def no_shutdown_interface(self, interface: str) -> dict:
        return self.set_interface_disabled(interface, False)

    def restart_interface(self, interface: str) -> dict:
        first = self.shutdown_interface(interface)
        second = self.no_shutdown_interface(interface)

        return {
            "ok": True,
            "operation": "restart",
            "interface": interface,
            "shutdown_result": first,
            "no_shutdown_result": second,
        }

    def _get_bridge_hosts(self) -> list[dict]:
        result = self._request("GET", "interface/bridge/host")

        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    def _is_bool_true(self, value) -> bool:
        return str(value).strip().lower() in ("true", "yes", "1")

    def _is_locally_administered_mac(self, mac: str) -> bool:
        try:
            first_octet = int(mac.split(":")[0], 16)
            return bool(first_octet & 0b10)
        except Exception:
            return False

    def get_mac_table(self, interface: str) -> list[dict]:
        hosts = self._get_bridge_hosts()

        seen = set()
        results = []

        for entry in hosts:
            if entry.get("on-interface") != interface:
                continue

            mac = entry.get("mac-address")
            if not mac:
                continue

            if self._is_bool_true(entry.get("local")):
                continue

            if mac in seen:
                continue
            seen.add(mac)

            if self._is_locally_administered_mac(mac):
                vendor = "Locally administered"
            else:
                vendor = lookup_mac_vendor(mac)

            results.append(
                {
                    "mac": mac,
                    "vendor": vendor,
                }
            )

        return results

    def get_dhcp_bindings(self) -> list[dict]:
        result = self._request("GET", "ip/dhcp-server/lease")

        if isinstance(result, dict):
            leases = [result]
        elif isinstance(result, list):
            leases = result
        else:
            leases = []

        out = []
        for lease in leases:
            dynamic = self._as_bool(lease.get("dynamic"))
            ip = lease.get("address")
            mac = lease.get("mac-address")
            expiration = lease.get("expires-after") or ""
            lease_type = "Automatic" if dynamic else "Manual"
            state = lease.get("status") or ""
            interface = lease.get("server") or ""

            out.append(
                {
                    "ID": lease.get(".id"),

                    "IP_ADDRESS": ip,
                    "HARDWARE_ADDRESS": mac,
                    "EXPIRATION": expiration,
                    "TYPE": lease_type,
                    "STATE": state,
                    "INTERFACE": interface,

                    "ip": ip,
                    "mac": mac,
                    "lease_until": expiration,
                    "type": lease_type,
                    "state": state,
                    "interface": interface,

                    "RAW": lease,
                }
            )

        return out

    def clear_interface_counters(self, interface: str) -> dict:
        iface = self.get_interface(interface)
        if not iface:
            raise ValueError(f"Interface '{interface}' not found")

        iface_type = str(iface.get("type", "")).lower()
        if iface_type != "ether":
            raise ValueError(
                f"Reset counters je podporovaný len pre ethernet interfaces, '{interface}' je typu '{iface_type}'."
            )

        attempts = [
            {"numbers": interface},
            {"numbers": iface.get(".id")},
            {".id": iface.get(".id")},
            {"name": interface},
        ]

        last_error = None
        result = None

        for payload in attempts:
            payload = {k: v for k, v in payload.items() if v}
            if not payload:
                continue
            try:
                result = self._request(
                    "POST",
                    "interface/ethernet/reset-counters",
                    json=payload,
                )
                break
            except Exception as e:
                last_error = e

        if result is None and last_error is not None:
            raise last_error

        verify = self.get_interface(interface)

        return {
            "ok": True,
            "operation": "clear-counters",
            "interface": interface,
            "result": result,
            "verify": {
                "rx-byte": verify.get("rx-byte") if verify else None,
                "tx-byte": verify.get("tx-byte") if verify else None,
                "rx-packet": verify.get("rx-packet") if verify else None,
                "tx-packet": verify.get("tx-packet") if verify else None,
                "tx-queue-drop": verify.get("tx-queue-drop") if verify else None,
            },
        }

    def clear_mac_table(
            self,
            *,
            platform: str,
            interface: str | None = None,
            vlan: int | None = None,
            dynamic_only: bool = True,
    ) -> dict:
        raise NotImplementedError("MikroTik REST driver zatiaľ nepodporuje clear MAC table.")

    def clear_dhcp_binding(self, ip_address: str) -> dict:
        leases = self.get_dhcp_bindings()

        target = None
        for lease in leases:
            if lease.get("IP_ADDRESS") == ip_address or lease.get("ip") == ip_address:
                target = lease
                break

        if not target:
            raise ValueError(f"DHCP lease for IP '{ip_address}' not found")

        lease_id = target.get("ID") or target.get("id")
        if not lease_id:
            raise ValueError(f"DHCP lease for IP '{ip_address}' has no lease ID")

        self._request("DELETE", f"ip/dhcp-server/lease/{lease_id}")

        return {
            "ok": True,
            "operation": "clear-dhcp-binding",
            "ip_address": ip_address,
            "lease_id": lease_id,
            "result": "deleted",
        }

    def get_interface_counters(self, interface: str) -> list[dict]:
        iface = self.get_interface(interface)

        if not iface:
            return []

        return [
            {
                "INTERFACE": interface,
                "INPUT_BYTES": iface.get("rx-byte"),
                "OUTPUT_BYTES": iface.get("tx-byte"),
                "INPUT_PACKETS": iface.get("rx-packet"),
                "OUTPUT_PACKETS": iface.get("tx-packet"),
                "INPUT_ERRORS": iface.get("rx-error"),
                "OUTPUT_ERRORS": iface.get("tx-error"),
                "INPUT_DROPS": iface.get("rx-drop"),
                "OUTPUT_DROPS": iface.get("tx-drop"),
                "QUEUE_DROPS": iface.get("tx-queue-drop"),
                "LINK_DOWNS": iface.get("link-downs"),
                "RAW": iface,
            }
        ]

    def show_optics(self, interface: str) -> dict:
        raise NotImplementedError("MikroTik REST driver zatiaľ nepodporuje optics.")

    def get_running_interface_block(self, interface: str) -> list[str]:
        raise NotImplementedError("MikroTik REST driver zatiaľ nepodporuje running-config bloky.")

    def send_config_lines(self, lines: list[str]) -> str:
        raise NotImplementedError("MikroTik REST driver zatiaľ nepodporuje send_config_lines.")

    def enable_netconf(self) -> dict:
        raise NotImplementedError("MikroTik nepodporuje NETCONF v tomto driveri.")

    def check_netconf_enabled(self) -> dict:
        return {
            "ok": False,
            "operation": "check-netconf",
            "netconf_yang_present": False,
            "netconf_ssh_present": False,
            "netconf_session_available": False,
            "verify_output": "",
            "message": "MikroTik REST driver nepodporuje NETCONF.",
        }

    def close(self) -> None:
        self.session.close()
