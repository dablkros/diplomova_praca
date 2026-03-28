from __future__ import annotations

from abc import ABC, abstractmethod

from backend.drivers.capabilities import DeviceCapabilities


class BaseDeviceDriver(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> DeviceCapabilities:
        raise NotImplementedError

    @abstractmethod
    def get_interface_state(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_interface_counters(self, interface: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_mac_table(self, interface: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_dhcp_bindings(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def clear_interface_counters(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def shutdown_interface(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def no_shutdown_interface(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def restart_interface(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def clear_mac_table(
        self,
        *,
        platform: str,
        interface: str | None = None,
        vlan: int | None = None,
        dynamic_only: bool = True,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def clear_dhcp_binding(self, ip_address: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_running_interface_block(self, interface: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def show_optics(self, interface: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def send_config_lines(self, lines: list[str]) -> str:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError