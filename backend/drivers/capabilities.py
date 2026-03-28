from __future__ import annotations

from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class DeviceCapabilities:
    supports_interface_state: bool = False
    supports_counters: bool = False
    supports_mac_table: bool = False
    supports_dhcp_bindings: bool = False
    supports_clear_dhcp_binding: bool = False
    supports_admin_toggle: bool = False
    supports_restart_interface: bool = False
    supports_clear_counters: bool = False
    supports_clear_mac_table: bool = False
    supports_optics: bool = False
    supports_config_compare: bool = False
    supports_config_apply: bool = False

    supports_netconf: bool = False
    supports_rest_api: bool = False
    supports_ssh_cli: bool = False

    def to_dict(self) -> dict:
        return asdict(self)