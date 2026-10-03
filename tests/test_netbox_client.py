import unittest
from unittest.mock import Mock, patch

from backend.clients.netbox_client import NetBoxClient


class NetBoxClientTest(unittest.TestCase):
    @patch("backend.clients.netbox_client.requests.Session")
    def test_get_uses_session_and_timeout(self, session_cls):
        session = Mock()
        response = Mock()
        response.json.return_value = {"results": []}
        session.get.return_value = response
        session_cls.return_value = session

        client = NetBoxClient(base_url="http://netbox.local", headers={"Authorization": "Token x"}, timeout=15)
        result = client._get("/api/dcim/devices/", limit=100)

        self.assertEqual(result, {"results": []})
        session.get.assert_called_once_with(
            "http://netbox.local/api/dcim/devices/",
            params={"limit": 100},
            timeout=15,
        )
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
