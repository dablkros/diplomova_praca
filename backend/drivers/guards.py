from backend.drivers.base import BaseDeviceDriver
from backend.drivers.errors import CapabilityNotSupportedError


def require_capability(
    driver: BaseDeviceDriver,
    capability_name: str,
    message: str | None = None,
) -> None:
    caps = driver.capabilities

    if not hasattr(caps, capability_name):
        raise RuntimeError(f"Unknown capability '{capability_name}'")

    if not getattr(caps, capability_name):
        raise CapabilityNotSupportedError(
            capability_name=capability_name,
            message=message,
        )