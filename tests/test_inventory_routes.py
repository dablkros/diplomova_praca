import unittest

from backend.api.routes import inventory


class InventoryRouteHelpersTest(unittest.TestCase):
    def test_primary_ip_returns_ipv4_without_prefix(self):
        device = {"primary_ip4": {"address": "10.0.0.1/24"}}
        self.assertEqual(inventory._primary_ip(device), "10.0.0.1")

    def test_device_summary_maps_fields(self):
        device = {
            "name": "sw-01",
            "primary_ip4": {"address": "192.168.1.5/24"},
            "device_type": {
                "manufacturer": {"name": "Cisco"},
                "model": "C9300",
            },
            "platform": {"slug": "ios-xe", "name": "IOS XE"},
        }

        result = inventory._device_summary(device)

        self.assertEqual(result["name"], "sw-01")
        self.assertEqual(result["ip"], "192.168.1.5")
        self.assertEqual(result["manufacturer"], "Cisco")
        self.assertEqual(result["platform"], "ios-xe")
        self.assertEqual(result["model"], "C9300")

    def test_compact_device_with_site_handles_missing_site(self):
        device = {
            "name": "sw-02",
            "primary_ip4": {"address": "172.16.0.2/24"},
            "site": None,
        }

        result = inventory._compact_device_with_site(device)

        self.assertEqual(result, {"name": "sw-02", "ip": "172.16.0.2", "site": None})


if __name__ == "__main__":
    unittest.main()
