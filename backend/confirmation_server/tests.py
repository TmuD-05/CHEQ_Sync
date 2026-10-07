from django.test import TestCase, override_settings
from unittest.mock import patch, MagicMock
from confirmation_server.services import get_access_token, _token_cache, ConfirmationService
from confirmation_server.auth import Auth0User
from rest_framework.test import APIClient
import time
import os
import jwt

@override_settings(
    AUTH0_DOMAIN="mock-tenant.auth0.com",
    AUTH0_CLIENT_ID="mock_client_id",
    AUTH0_CLIENT_SECRET="mock_client_secret",
    AUTH0_AUDIENCE="mock_audience"
)
class ConfirmationServerTokenTests(TestCase):
    def setUp(self):
        # Reset token cache before each test
        _token_cache["access_token"] = None
        _token_cache["expires_at"] = 0

    @patch('confirmation_server.services.requests.post')
    def test_token_caching_and_refresh(self, mock_post):
        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_mock_token",
            "expires_in": 3600
        }
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # 1. Fetch token first time (should hit network/mock_post)
        token1 = get_access_token()
        self.assertEqual(token1, "new_mock_token")
        self.assertEqual(mock_post.call_count, 1)

        # 2. Fetch token second time immediately (should return cached token, call count stays 1)
        token2 = get_access_token()
        self.assertEqual(token2, "new_mock_token")
        self.assertEqual(mock_post.call_count, 1)

        # 3. Simulate expired token cache
        _token_cache["expires_at"] = time.time() - 100 # expired
        mock_response.json.return_value = {
            "access_token": "refreshed_mock_token",
            "expires_in": 3600
        }

        # 4. Fetch token third time (should trigger refresh/mock_post)
        token3 = get_access_token()
        self.assertEqual(token3, "refreshed_mock_token")
        self.assertEqual(mock_post.call_count, 2)


class ConfirmationServiceTests(TestCase):
    def setUp(self):
        from core.crypto import get_cs_key_manager, get_rs_key_manager, get_jwks_cache
        self.cs_km = get_cs_key_manager()
        self.rs_km = get_rs_key_manager()
        self.cs_public_pem = self.cs_km.export_signing_public_pem().decode("utf-8")
        self.rs_private_pem = self.rs_km.export_signing_private_pem()
        self.rs_kid = self.rs_km.sig_kid

        self.cache = get_jwks_cache()
        self.cache.register_jwks("http://127.0.0.1:8000/resource_server/.well-known/jwks.json", self.rs_km.get_public_jwks())
        self.cache.register_jwks("http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json", self.cs_km.get_public_jwks())

    def test_generate_service_jwt(self):
        token = ConfirmationService.generate_service_jwt()
        decoded = jwt.decode(
            token,
            self.cs_public_pem,
            algorithms=["RS256"],
            audience="resource_server",
            issuer="confirmation_server"
        )
        self.assertEqual(decoded["iss"], "confirmation_server")
        self.assertEqual(decoded["aud"], "resource_server")

    @patch('confirmation_server.services.requests.get')
    def test_retrieve_cheq_normalizes_url_and_decodes(self, mock_get):
        cheq_payload = {"CHEQ": {"version": 1.0, "operation": "flight_booking", "inputs": {"parameters": []}}}
        encoded_cheq = jwt.encode(cheq_payload, self.rs_private_pem, algorithm="RS256", headers={"kid": self.rs_kid})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = encoded_cheq
        mock_resp.text = f'"{encoded_cheq}"'
        mock_get.return_value = mock_resp

        # Test with trailing slash
        result = ConfirmationService.retrieveCHEQ("http://127.0.0.1:8000/resource_server/resource/abc/")
        self.assertEqual(result, cheq_payload)
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, "http://127.0.0.1:8000/resource_server/resource/abc/cheq/")

        # Test without trailing slash
        result2 = ConfirmationService.retrieveCHEQ("http://127.0.0.1:8000/resource_server/resource/abc")
        self.assertEqual(result2, cheq_payload)
        called_url2 = mock_get.call_args[0][0]
        self.assertEqual(called_url2, "http://127.0.0.1:8000/resource_server/resource/abc/cheq/")


class TriggerViewEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = Auth0User({"sub": "auth0|test-user"})
        self.client.force_authenticate(user=self.user)

    @patch('confirmation_server.services.ConfirmationService.retrieveCHEQ')
    def test_trigger_view_success(self, mock_retrieve):
        mock_retrieve.return_value = {
            "CHEQ": {
                "version": 1.0,
                "inputs": {
                    "parameters": [{"selected_flight": {"airline": "United", "price": 500}}]
                }
            }
        }
        response = self.client.post(
            "/confirmation_server/trigger_confirmation/",
            data={"resource_uri": "http://127.0.0.1:8000/resource_server/resource/abc/"},
            format="json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("CHEQ", data)
        self.assertIn("perform_confirmation_uri", data)

    @patch('confirmation_server.services.ConfirmationService.retrieveCHEQ')
    def test_trigger_view_404_when_cheq_missing(self, mock_retrieve):
        mock_retrieve.side_effect = IndexError
        response = self.client.post(
            "/confirmation_server/trigger_confirmation/",
            data={"resource_uri": "http://127.0.0.1:8000/resource_server/resource/missing/"},
            format="json"
        )
        self.assertEqual(response.status_code, 404)


class PerformViewEndpointTests(TestCase):
    def setUp(self):
        from core.crypto import get_cs_key_manager, get_rs_key_manager, get_jwks_cache
        self.client = APIClient()
        self.user = Auth0User({"sub": "auth0|test-user"})
        self.client.force_authenticate(user=self.user)
        self.rs_km = get_rs_key_manager()
        self.cs_km = get_cs_key_manager()
        self.cache = get_jwks_cache()
        self.cache.register_jwks("http://127.0.0.1:8000/resource_server/.well-known/jwks.json", self.rs_km.get_public_jwks())
        self.cache.register_jwks("http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json", self.cs_km.get_public_jwks())

    @patch('confirmation_server.services.requests.post')
    def test_send_decision_to_rs_includes_auth_header(self, mock_post):
        from core.crypto import NestedTokenService
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        ConfirmationService.sendDecisionToRS(
            CHEQ={"version": 1.0},
            decision="ACCEPT",
            resource_uri="http://127.0.0.1:8000/resource_server/resource/abc/",
            auth_token="Bearer mock_user_token"
        )
        self.assertEqual(mock_post.call_count, 1)
        call_kwargs = mock_post.call_args[1]
        headers = call_kwargs["headers"]
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], "Bearer mock_user_token")

        # Verify decision is bound inside signed_CHEQ payload and not in URL params
        self.assertNotIn("params", call_kwargs)
        signed_token = call_kwargs["data"]["signed_CHEQ"]
        decrypted = NestedTokenService.decrypt_and_verify_nested_token(
            token_string=signed_token,
            recipient_enc_key=self.rs_km,
            jwks_cache=self.cache,
            sender_jwks_url="http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json",
            expected_issuer="confirmation_server",
            expected_audience="resource_server"
        )
        self.assertEqual(decrypted["decision"], "ACCEPT")
        self.assertEqual(decrypted["CHEQ"], {"version": 1.0})

    @patch('confirmation_server.services.requests.post')
    def test_send_decision_to_rs_fallback_m2m(self, mock_post):
        from core.crypto import NestedTokenService
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        with patch('confirmation_server.services.get_access_token', return_value="mock_m2m_token"):
            ConfirmationService.sendDecisionToRS(
                CHEQ={"version": 1.0},
                decision="ACCEPT",
                resource_uri="http://127.0.0.1:8000/resource_server/resource/abc/"
            )
            self.assertEqual(mock_post.call_count, 1)
            call_kwargs = mock_post.call_args[1]
            headers = call_kwargs["headers"]
            self.assertIn("Authorization", headers)
            self.assertEqual(headers["Authorization"], "Bearer mock_m2m_token")

            # Verify decision is bound inside signed_CHEQ payload and not in URL params
            self.assertNotIn("params", call_kwargs)
            signed_token = call_kwargs["data"]["signed_CHEQ"]
            decrypted = NestedTokenService.decrypt_and_verify_nested_token(
                token_string=signed_token,
                recipient_enc_key=self.rs_km,
                jwks_cache=self.cache,
                sender_jwks_url="http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json",
                expected_issuer="confirmation_server",
                expected_audience="resource_server"
            )
            self.assertEqual(decrypted["decision"], "ACCEPT")
            self.assertEqual(decrypted["CHEQ"], {"version": 1.0})

    def test_cs_jwks_endpoint_returns_keys(self):
        """
        GET /confirmation_server/.well-known/jwks.json returns 200 OK and valid JWKS
        """
        response = self.client.get("/confirmation_server/.well-known/jwks.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cache-Control", response.headers)
        data = response.json()
        self.assertIn("keys", data)
        self.assertEqual(len(data["keys"]), 2)
        uses = {k["use"] for k in data["keys"]}
        self.assertEqual(uses, {"sig", "enc"})

    @patch('confirmation_server.services.requests.get')
    def test_retrieve_cheq_nested_jwe(self, mock_get):
        """
        ConfirmationService.retrieveCHEQ decrypts and verifies a 5-part nested JWE
        """
        from core.crypto import get_rs_key_manager, get_cs_key_manager, NestedTokenService
        rs_km = get_rs_key_manager()
        cs_km = get_cs_key_manager()

        cheq_payload = {"CHEQ": {"version": 1.0, "operation": "flight_booking", "flight_id": "FL999"}}
        nested_jwe = NestedTokenService.create_nested_token(
            payload=cheq_payload,
            signer_key=rs_km.signing_key,
            recipient_enc_key=cs_km.encryption_key,
            issuer="resource_server",
            audience="confirmation_server"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/jose+json"}
        mock_resp.json.return_value = nested_jwe
        mock_resp.text = f'"{nested_jwe}"'
        mock_get.return_value = mock_resp

        result = ConfirmationService.retrieveCHEQ("http://127.0.0.1:8000/resource_server/resource/abc/")
        self.assertEqual(result["CHEQ"]["flight_id"], "FL999")

    def test_sign_creates_nested_jwe_by_default(self):
        """
        ConfirmationService.sign generates a 5-part nested JWE when encrypt_for_rs=True
        """
        nested_token = ConfirmationService.sign({"test": "data"}, encrypt_for_rs=True)
        self.assertEqual(len(nested_token.split(".")), 5)

