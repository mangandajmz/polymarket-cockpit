import unittest
from unittest.mock import Mock, patch

from api_client import JsonApiClient


class ApiClientTests(unittest.TestCase):
    def test_get_json_retries_and_returns_json(self):
        client = JsonApiClient(default_timeout=1.0, default_retries=3, backoff_base=0.0, jitter_max=0.0)

        good_response = Mock()
        good_response.raise_for_status.return_value = None
        good_response.json.return_value = {"ok": True}

        with patch.object(client.session, "get", side_effect=[Exception("boom"), good_response]) as mock_get:
            result = client.get_json("https://example.com")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_get.call_count, 2)

    def test_session_ignores_ambient_proxy_by_default(self):
        client = JsonApiClient()

        self.assertFalse(client.session.trust_env)

    def test_session_can_opt_into_environment_settings(self):
        client = JsonApiClient(trust_env=True)

        self.assertTrue(client.session.trust_env)


if __name__ == "__main__":
    unittest.main()
