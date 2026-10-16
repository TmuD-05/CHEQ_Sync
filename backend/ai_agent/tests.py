import time
from django.test import TestCase, override_settings
from unittest.mock import patch, MagicMock
from ai_agent.services import get_ai_agent_access_token, _ai_token_cache, AgentService


@override_settings(
    AUTH0_DOMAIN="dev-q172gr40txifrff3.us.auth0.com",
    AI_AGENT_CLIENT_ID="mock_ai_agent_client_id",
    AI_AGENT_CLIENT_SECRET="mock_ai_agent_client_secret",
    AUTH0_AUDIENCE="https://cheq-sync-api",
)
class AIAgentM2MTokenTests(TestCase):
    def setUp(self):
        _ai_token_cache["access_token"] = None
        _ai_token_cache["expires_at"] = 0

    @patch("ai_agent.services.httpx.post")
    def test_ai_agent_token_caching_and_refresh(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "ai_agent_mock_jwt_1",
            "expires_in": 3600,
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # 1. Initial call fetches token from Auth0 /oauth/token
        token1 = get_ai_agent_access_token()
        self.assertEqual(token1, "ai_agent_mock_jwt_1")
        self.assertEqual(mock_post.call_count, 1)

        called_url = mock_post.call_args[0][0]
        called_payload = mock_post.call_args[1]["json"]
        self.assertEqual(called_url, "https://dev-q172gr40txifrff3.us.auth0.com/oauth/token")
        self.assertEqual(called_payload["client_id"], "mock_ai_agent_client_id")
        self.assertEqual(called_payload["client_secret"], "mock_ai_agent_client_secret")
        self.assertEqual(called_payload["audience"], "https://cheq-sync-api")
        self.assertEqual(called_payload["grant_type"], "client_credentials")

        # 2. Second immediate call uses cached token
        token2 = get_ai_agent_access_token()
        self.assertEqual(token2, "ai_agent_mock_jwt_1")
        self.assertEqual(mock_post.call_count, 1)

        # 3. Expire cached token and verify refresh
        _ai_token_cache["expires_at"] = time.time() - 50
        mock_response.json.return_value = {
            "access_token": "ai_agent_mock_jwt_2",
            "expires_in": 3600,
        }
        token3 = get_ai_agent_access_token()
        self.assertEqual(token3, "ai_agent_mock_jwt_2")
        self.assertEqual(mock_post.call_count, 2)

    @patch("ai_agent.services.get_ai_agent_access_token", return_value="mock_bearer_jwt")
    @patch("ai_agent.services.httpx.post")
    def test_execute_cheq_flow_attaches_bearer_token(self, mock_post, _mock_token):
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.json.return_value = {
            "confirmation_uri": "http://127.0.0.1:8000/confirmation_server/trigger_confirmation/",
            "resource_uri": "http://127.0.0.1:8000/resource_server/resource/f47ac10b-58cc-4372-a567-0e02b2c3d479/",
            "result_uri": "http://127.0.0.1:8000/resource_server/result/f47ac10b-58cc-4372-a567-0e02b2c3d479/",
            "flights": {"best_flights": []},
        }
        mock_post.return_value = mock_resp

        agent = AgentService()
        result = agent.execute_cheq_flow(
            {"origin": "YVR", "destination": "NRT", "outbound_date": "2026-06-25", "return_date": "2026-07-05", "type": 1},
            session_id="test_session",
        )
        self.assertEqual(result, {"best_flights": []})
        headers = mock_post.call_args[1].get("headers", {})
        self.assertEqual(headers.get("Authorization"), "Bearer mock_bearer_jwt")

    @patch("ai_agent.services.get_ai_agent_access_token", return_value="mock_bearer_jwt")
    @patch("ai_agent.services.httpx.get")
    def test_poll_for_result_attaches_bearer_token(self, mock_get, _mock_token):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"confirmation_status": "ACCEPT"}]
        mock_get.return_value = mock_resp

        agent = AgentService()
        agent.uri_pack = {
            "confirmation_uri": "http://127.0.0.1:8000/confirmation_server/trigger_confirmation/",
            "resource_uri": "http://127.0.0.1:8000/resource_server/resource/f47ac10b-58cc-4372-a567-0e02b2c3d479/",
            "result_uri": "http://127.0.0.1:8000/resource_server/result/f47ac10b-58cc-4372-a567-0e02b2c3d479/",
        }
        agent._save_uri_pack("test_poll_session")

        outcome = agent.poll_for_result(session_id="test_poll_session", max_attempts=1, interval=0)
        self.assertIn("approved", outcome)
        headers = mock_get.call_args[1].get("headers", {})
        self.assertEqual(headers.get("Authorization"), "Bearer mock_bearer_jwt")
