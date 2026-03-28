from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.clients.netbox_client import NetBoxClient
from backend.core.platforms import get_drivers_for_device
from backend.schemas.common import DeviceInfo, ClearMacTableRequest
from backend.services.device_service import make_driver_from_identity
from backend.drivers.guards import require_capability
from backend.drivers.errors import CapabilityNotSupportedError

router = APIRouter(tags=["ops"])
netbox = NetBoxClient()

def _make_driver_for_device(data: DeviceInfo):
    driver, _, _ = make_driver_from_identity(
        netbox,
        device_name=data.device_name,
        host=data.host,
        username=data.username,
        password=data.password,
    )
    return driver

@router.post("/check_macaddress")
def over_mac_adresu(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_mac_table",
            "Toto zariadenie nepodporuje MAC tabuľku.",
        )
        macs = driver.get_mac_table(data.interface)
        return {
            "stav": f"MAC adresy na porte {data.interface}",
            "mac_adresy": macs,
            "capabilities": driver.capabilities.to_dict(),
        }
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()

@router.post("/show-counters")
def show_interface_counters(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_counters",
            "Toto zariadenie nepodporuje counters.",
        )
        counters = driver.get_interface_counters(data.interface)
        return {
            "stav": f"Packet counters pre {data.interface}",
            "counters": counters,
            "capabilities": driver.capabilities.to_dict(),
        }
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()

@router.post("/show-status-int")
def show_interface_status(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_interface_state",
            "Toto zariadenie nepodporuje čítanie stavu interface.",
        )
        state = driver.get_interface_state(data.interface)
        return {
            "stav": f"Stav interface {data.interface}",
            "state": state,
            "capabilities": driver.capabilities.to_dict(),
        }
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()

@router.post("/show-dhcp-bindings")
def show_dhcp_bindings(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_dhcp_bindings",
            "Toto zariadenie nepodporuje čítanie DHCP bindings.",
        )
        bindings = driver.get_dhcp_bindings()
        return {
            "stav": "DHCP bindings",
            "bindings": bindings,
            "capabilities": driver.capabilities.to_dict(),
        }
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/clear-dhcp-binding")
def clear_dhcp_binding(data: DeviceInfo):
    if not data.ip_address:
        raise HTTPException(status_code=400, detail="Chýba IP adresa")

    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_clear_dhcp_binding",
            "Toto zariadenie nepodporuje clear DHCP binding.",
        )
        return driver.clear_dhcp_binding(data.ip_address)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/clear-counters")
def clear_interface_counters(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_clear_counters",
            "Toto zariadenie nepodporuje clear counters.",
        )
        return driver.clear_interface_counters(data.interface)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/shutdown")
def shutdown_interface(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_admin_toggle",
            "Toto zariadenie nepodporuje shutdown/no shutdown.",
        )
        return driver.shutdown_interface(data.interface)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/no-shutdown")
def no_shutdown_interface(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_admin_toggle",
            "Toto zariadenie nepodporuje shutdown/no shutdown.",
        )
        return driver.no_shutdown_interface(data.interface)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/restart-interface")
def restart_interface(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        require_capability(
            driver,
            "supports_restart_interface",
            "Toto zariadenie nepodporuje restart interface.",
        )
        return driver.restart_interface(data.interface)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/mac-table/clear")
def clear_mac_table(data: ClearMacTableRequest):
    driver, _, resolved_device_name = make_driver_from_identity(
        netbox,
        device_name=data.device_name,
        host=data.host,
        username=data.username,
        password=data.password,
    )

    try:
        require_capability(
            driver,
            "supports_clear_mac_table",
            "Toto zariadenie nepodporuje clear MAC table.",
        )

        drivers = get_drivers_for_device(netbox, resolved_device_name)

        return driver.clear_mac_table(
            platform=drivers["platform"],
            interface=data.interface,
            vlan=data.vlan,
            dynamic_only=data.dynamic_only,
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/device-capabilities")
def device_capabilities(data: DeviceInfo):
    driver = _make_driver_for_device(data)

    try:
        return {
            "device": data.device_name,
            "host": data.host,
            "capabilities": driver.capabilities.to_dict(),
        }
    finally:
        driver.close()