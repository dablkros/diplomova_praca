import unittest

from backend.api.routes import ws


class WsHelpersTest(unittest.TestCase):
    def test_safe_int_with_invalid_value_returns_default(self):
        self.assertEqual(ws.safe_int("abc", default=7), 7)

    def test_safe_int_with_valid_value_returns_int(self):
        self.assertEqual(ws.safe_int("42"), 42)

    def test_is_optical_interface_is_case_insensitive(self):
        self.assertTrue(ws.is_optical_interface("SFP+"))

    def test_is_optical_interface_false_for_none(self):
        self.assertFalse(ws.is_optical_interface(None))


if __name__ == "__main__":
    unittest.main()
