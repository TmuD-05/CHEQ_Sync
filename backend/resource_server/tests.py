import os
import time
import uuid
import jwt
import datetime

from django.test import TestCase, Client
from django.utils import timezone
from django.core import signing
from django.urls import reverse

from .models import Resource, Process
from .views import IndexView

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ResourceModelTests(TestCase):
    fixtures = ['initial_resources.json']

    def test_process_1_has_3_items(self):
        """
        process 1 has 3 steps in it
        """
        process_1 = Resource.get_all_steps_in_process(process_id="f47ac10b-58cc-4372-a567-0e02b2c3d479")
        self.assertEqual(len(process_1), 3)


from unittest.mock import patch
from rest_framework.test import APITestCase

class ResourceServerAuthTests(APITestCase):
    fixtures = ['initial_resources.json']

    def setUp(self):
        self.process_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    def test_get_cheq_without_auth_header_fails(self):
        url = reverse('resource_server:resource_cheq', kwargs={"process_id": self.process_id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [401, 403])

    def test_get_cheq_with_invalid_auth_header_fails(self):
        url = reverse('resource_server:resource_cheq', kwargs={"process_id": self.process_id})
        response = self.client.get(url, HTTP_AUTHORIZATION="invalid_format")
        self.assertIn(response.status_code, [401, 403])

    def test_post_decision_without_auth_header_fails(self):
        url = reverse('resource_server:resource', kwargs={"process_id": self.process_id})
        response = self.client.post(url, data={"signed_CHEQ": "token"}, QUERY_STRING="decision=ACCEPT")
        self.assertIn(response.status_code, [200, 400, 401, 403, 422])


class SecurityAuthenticationTests(TestCase):
    def setUp(self):
        from core.crypto import get_cs_key_manager, get_jwks_cache
        self.client = Client()
        self.process = Process.objects.create()
        Resource.objects.create(
            process_id=self.process.id,
            pub_date=timezone.now(),
            selected_flight={"airline": "United", "price": 1200.0}
        )
        cs_km = get_cs_key_manager()
        self.cs_private_pem = cs_km.export_signing_private_pem()
        self.cs_kid = cs_km.sig_kid
        cache = get_jwks_cache()
        cs_jwks_url = "http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json"
        cache.register_jwks(cs_jwks_url, cs_km.get_public_jwks())

    def generate_valid_cs_token(self):
        payload = {
            "iss": "confirmation_server",
            "aud": "resource_server",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60
        }
        headers = {"kid": self.cs_kid}
        return jwt.encode(payload, self.cs_private_pem, algorithm="RS256", headers=headers)

    def test_resource_cheq_unauthenticated_denied(self):
        """
        GET request without Authorization header must return 401 or 403 permission denied
        """
        url = reverse("resource_server:resource_cheq", kwargs={"process_id": self.process.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [401, 403])

    def test_resource_cheq_authenticated_success(self):
        """
        GET request with valid CS Service JWT returns 200 OK and signed CHEQ object
        """
        url = reverse("resource_server:resource_cheq", kwargs={"process_id": self.process.id})
        token = self.generate_valid_cs_token()
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)

    def test_cheq_contains_nonce_and_exp(self):
        """
        GET /resource_server/resource/<uuid>/cheq/ returns signed CHEQ containing nonce and exp
        """
        from core.crypto import get_rs_key_manager
        url = reverse("resource_server:resource_cheq", kwargs={"process_id": self.process.id})
        token = self.generate_valid_cs_token()
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        signed_cheq_str = response.json()
        
        rs_km = get_rs_key_manager()
        rs_pub_key = rs_km.export_signing_public_pem().decode("utf-8")
        decoded = jwt.decode(signed_cheq_str, rs_pub_key, algorithms=["RS256"])
        cheq = decoded["CHEQ"]
        self.assertIn("nonce", cheq)
        self.assertIn("exp", cheq)
        self.assertTrue(len(cheq["nonce"]) > 10)
        self.assertTrue(cheq["exp"] > time.time())

    @patch('resource_server.services.SignatureService.verify_auth0_token')
    def test_select_flight_on_accepted_process_returns_409(self, mock_verify):
        """
        POST to select_flight on an already finalized/accepted process returns 409 Conflict
        """
        from .models import Result
        mock_verify.return_value = {
            "sub": "ai_agent_client@clients",
            "azp": "ai_agent_client",
            "gty": "client-credentials"
        }
        Result.objects.create(process_id=self.process.id, confirmation_status="ACCEPT")
        url = reverse("resource_server:select_flight", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"selected_flight": "Lufthansa LU561 - $280"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer mock_ai_agent_token"
        )
        self.assertEqual(response.status_code, 409)

    @patch('resource_server.services.SignatureService.verify_auth0_token')
    def test_select_flight_creates_new_process_when_previously_rejected(self, mock_verify):
        """
        POST to select_flight when previous choice was rejected spawns an isolated new Process
        leaving the original process permanently marked as REJECT.
        """
        from .models import Result
        mock_verify.return_value = {
            "sub": "ai_agent_client@clients",
            "azp": "ai_agent_client",
            "gty": "client-credentials"
        }
        Result.objects.create(process_id=self.process.id, confirmation_status="REJECT")
        url = reverse("resource_server:select_flight", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"selected_flight": "Lufthansa LU561 - $280"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer mock_ai_agent_token"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("resource_uri", data)
        self.assertIn("result_uri", data)
        new_process_id = data["process_id"]
        self.assertNotEqual(str(new_process_id), str(self.process.id))

        # Original process remains untouched and permanently REJECT
        orig_result = Result.objects.get(process_id=self.process.id)
        self.assertEqual(orig_result.confirmation_status, "REJECT")

        # New process is created with fresh PENDING status
        new_result = Result.objects.get(process_id=new_process_id)
        self.assertEqual(new_result.confirmation_status, "PENDING")

    def test_jwks_endpoint_returns_separated_keys(self):
        """
        GET /resource_server/.well-known/jwks.json returns 200 OK, valid JWKS, and cache headers
        """
        url = reverse("resource_server:jwks")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cache-Control", response.headers)
        self.assertIn("max-age=3600", response.headers["Cache-Control"])
        data = response.json()
        self.assertIn("keys", data)
        self.assertEqual(len(data["keys"]), 2)
        uses = {k["use"] for k in data["keys"]}
        self.assertEqual(uses, {"sig", "enc"})

    def test_cheq_nested_jwe_token_flow(self):
        """
        GET /resource_server/resource/<uuid>/cheq/ with Accept: application/jose+json
        returns 5-part Nested JWS-in-JWE token
        """
        from core.crypto import get_cs_key_manager, get_rs_key_manager, get_jwks_cache, NestedTokenService
        url = reverse("resource_server:resource_cheq", kwargs={"process_id": self.process.id})
        token = self.generate_valid_cs_token()
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_ACCEPT="application/jose+json")
        self.assertEqual(response.status_code, 200)
        nested_token_str = response.json()
        parts = nested_token_str.split(".")
        self.assertEqual(len(parts), 5, "Must be compact JWE (5 parts)")

        # Decrypt as Confirmation Server
        cs_km = get_cs_key_manager()
        rs_km = get_rs_key_manager()
        cache = get_jwks_cache()
        rs_jwks_url = "http://127.0.0.1:8000/resource_server/.well-known/jwks.json"
        cache.register_jwks(rs_jwks_url, rs_km.get_public_jwks())

        decrypted = NestedTokenService.decrypt_and_verify_nested_token(
            token_string=nested_token_str,
            recipient_enc_key=cs_km.encryption_key,
            jwks_cache=cache,
            sender_jwks_url=rs_jwks_url,
            expected_issuer="resource_server",
            expected_audience="confirmation_server"
        )
        self.assertIn("CHEQ", decrypted)
        cheq = decrypted["CHEQ"]
        self.assertIn("nonce", cheq)
        self.assertIn("exp", cheq)

    @patch('resource_server.views.check_auth0_token')
    def test_post_nested_jwe_decision_accepted(self, mock_auth):
        """
        POST decision with 5-part Nested JWS-in-JWE signed by CS and encrypted for RS.
        Decision is cryptographically bound inside the token payload (no query parameters).
        """
        from core.crypto import get_cs_key_manager, get_rs_key_manager, NestedTokenService
        from .models import Result
        mock_auth.return_value = {"sub": "user_123"}
        Result.objects.create(process_id=self.process.id, confirmation_status="PENDING")
        cs_km = get_cs_key_manager()
        rs_km = get_rs_key_manager()

        nonce = str(uuid.uuid4())
        cheq_payload = {
            "version": 1.0,
            "operation name": str(self.process.id),
            "nonce": nonce,
            "exp": int(time.time()) + 300
        }

        nested_token = NestedTokenService.create_nested_token(
            payload={"CHEQ": cheq_payload, "decision": "ACCEPT"},
            signer_key=cs_km.signing_key,
            recipient_enc_key=rs_km.encryption_key,
            issuer="confirmation_server",
            audience="resource_server"
        )

        url = reverse("resource_server:resource", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"signed_CHEQ": nested_token},
            HTTP_AUTHORIZATION="Bearer mock_token"
        )
        self.assertEqual(response.status_code, 200)
        res = Result.objects.get(process_id=self.process.id)
        self.assertEqual(res.confirmation_status, "ACCEPT")

    @patch('resource_server.views.check_auth0_token')
    def test_post_nested_jwe_decision_rejected(self, mock_auth):
        """
        POST decision REJECT bound cryptographically inside token payload.
        """
        from core.crypto import get_cs_key_manager, get_rs_key_manager, NestedTokenService
        from .models import Result
        mock_auth.return_value = {"sub": "user_123"}
        Result.objects.create(process_id=self.process.id, confirmation_status="PENDING")
        cs_km = get_cs_key_manager()
        rs_km = get_rs_key_manager()

        nonce = str(uuid.uuid4())
        cheq_payload = {
            "version": 1.0,
            "operation name": str(self.process.id),
            "nonce": nonce,
            "exp": int(time.time()) + 300
        }

        nested_token = NestedTokenService.create_nested_token(
            payload={"CHEQ": cheq_payload, "decision": "REJECT"},
            signer_key=cs_km.signing_key,
            recipient_enc_key=rs_km.encryption_key,
            issuer="confirmation_server",
            audience="resource_server"
        )

        url = reverse("resource_server:resource", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"signed_CHEQ": nested_token},
            HTTP_AUTHORIZATION="Bearer mock_token"
        )
        self.assertEqual(response.status_code, 200)
        res = Result.objects.get(process_id=self.process.id)
        self.assertEqual(res.confirmation_status, "REJECT")

    @patch('resource_server.views.check_auth0_token')
    def test_post_nested_jwe_missing_decision_rejected(self, mock_auth):
        """
        POST decision token missing 'decision' in payload is rejected with 422.
        """
        from core.crypto import get_cs_key_manager, get_rs_key_manager, NestedTokenService
        from .models import Result
        mock_auth.return_value = {"sub": "user_123"}
        Result.objects.create(process_id=self.process.id, confirmation_status="PENDING")
        cs_km = get_cs_key_manager()
        rs_km = get_rs_key_manager()

        nonce = str(uuid.uuid4())
        cheq_payload = {
            "version": 1.0,
            "operation name": str(self.process.id),
            "nonce": nonce,
            "exp": int(time.time()) + 300
        }

        # No 'decision' in token payload
        nested_token = NestedTokenService.create_nested_token(
            payload={"CHEQ": cheq_payload},
            signer_key=cs_km.signing_key,
            recipient_enc_key=rs_km.encryption_key,
            issuer="confirmation_server",
            audience="resource_server"
        )

        url = reverse("resource_server:resource", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"signed_CHEQ": nested_token},
            HTTP_AUTHORIZATION="Bearer mock_token"
        )
        self.assertEqual(response.status_code, 422)

    @patch('resource_server.views.check_auth0_token')
    def test_post_nested_jwe_invalid_decision_rejected(self, mock_auth):
        """
        POST decision token with invalid decision value (e.g. 'MAYBE') is rejected with 422.
        """
        from core.crypto import get_cs_key_manager, get_rs_key_manager, NestedTokenService
        from .models import Result
        mock_auth.return_value = {"sub": "user_123"}
        Result.objects.create(process_id=self.process.id, confirmation_status="PENDING")
        cs_km = get_cs_key_manager()
        rs_km = get_rs_key_manager()

        nonce = str(uuid.uuid4())
        cheq_payload = {
            "version": 1.0,
            "operation name": str(self.process.id),
            "nonce": nonce,
            "exp": int(time.time()) + 300
        }

        nested_token = NestedTokenService.create_nested_token(
            payload={"CHEQ": cheq_payload, "decision": "MAYBE"},
            signer_key=cs_km.signing_key,
            recipient_enc_key=rs_km.encryption_key,
            issuer="confirmation_server",
            audience="resource_server"
        )

        url = reverse("resource_server:resource", kwargs={"process_id": self.process.id})
        response = self.client.post(
            url,
            data={"signed_CHEQ": nested_token},
            HTTP_AUTHORIZATION="Bearer mock_token"
        )
        self.assertEqual(response.status_code, 422)


class AIAgentAuthenticationTests(TestCase):
    def setUp(self):
        from .models import Result
        self.client = Client()
        self.process = Process.objects.create()
        Resource.objects.create(
            process_id=self.process.id,
            pub_date=timezone.now(),
        )
        Result.objects.create(process_id=self.process.id, confirmation_status="PENDING")

    def test_ai_agent_endpoints_reject_unauthenticated_requests(self):
        """
        All 3 AI Agent endpoints on Resource Server must reject unauthenticated calls with 401/403.
        """
        exec_url = reverse("resource_server:execute_process_with_confirmation")
        select_url = reverse("resource_server:select_flight", kwargs={"process_id": self.process.id})
        result_url = reverse("resource_server:result", kwargs={"process_id": self.process.id})

        resp1 = self.client.post(exec_url, data={}, content_type="application/json")
        resp2 = self.client.post(select_url, data={"selected_flight": "UA100"}, content_type="application/json")
        resp3 = self.client.get(result_url)

        self.assertIn(resp1.status_code, [401, 403])
        self.assertIn(resp2.status_code, [401, 403])
        self.assertIn(resp3.status_code, [401, 403])

    @patch("resource_server.views.requests.post")
    @patch("resource_server.services.SignatureService.verify_auth0_token")
    def test_ai_agent_authenticated_endpoints_succeed(self, mock_verify, mock_airline_post):
        """
        Valid AI Agent M2M JWT (gty=client-credentials, @clients sub) is accepted
        across execute_process_with_confirmation (202), select_flight (200), and result (200)
        without the Resource Server needing to know AI_AGENT_CLIENT_ID in settings.
        """
        mock_verify.return_value = {
            "iss": "https://dev-q172gr40txifrff3.us.auth0.com/",
            "sub": "ai_agent_m2m_client_123@clients",
            "aud": "https://cheq-sync-api",
            "azp": "ai_agent_m2m_client_123",
            "gty": "client-credentials"
        }
        mock_airline_post.return_value.json.return_value = {"best_flights": []}

        # 1. Trigger confirmation (202 Accepted)
        exec_url = reverse("resource_server:execute_process_with_confirmation")
        resp_exec = self.client.post(
            exec_url,
            data={"origin": "YVR", "destination": "NRT"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid_ai_agent_jwt"
        )
        self.assertEqual(resp_exec.status_code, 202)
        self.assertIn("resource_uri", resp_exec.json())

        # 2. Select flight (200 OK)
        select_url = reverse("resource_server:select_flight", kwargs={"process_id": self.process.id})
        resp_select = self.client.post(
            select_url,
            data={"selected_flight": "ANA NH135 - $1,742"},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer valid_ai_agent_jwt"
        )
        self.assertEqual(resp_select.status_code, 200)

        # 3. Poll result (200 OK)
        result_url = reverse("resource_server:result", kwargs={"process_id": self.process.id})
        resp_result = self.client.get(
            result_url,
            HTTP_AUTHORIZATION="Bearer valid_ai_agent_jwt"
        )
        self.assertEqual(resp_result.status_code, 200)

    @patch("resource_server.services.SignatureService.verify_auth0_token")
    def test_human_user_token_rejected_on_ai_agent_endpoint(self, mock_verify):
        """
        A human user token (missing gty=client-credentials and @clients subject) is rejected with 401/403.
        """
        mock_verify.return_value = {
            "iss": "https://dev-q172gr40txifrff3.us.auth0.com/",
            "sub": "auth0|64a9f1234567890",
            "aud": "https://cheq-sync-api"
        }
        result_url = reverse("resource_server:result", kwargs={"process_id": self.process.id})
        resp = self.client.get(result_url, HTTP_AUTHORIZATION="Bearer human_user_jwt")
        self.assertIn(resp.status_code, [401, 403])






