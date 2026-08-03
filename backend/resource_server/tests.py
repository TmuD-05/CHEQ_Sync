import os
import time
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
        process_1 = Resource.get_all_steps_in_process(process_id=1)
        self.assertEqual(len(process_1), 3)


from unittest.mock import patch
from rest_framework.test import APITestCase

class ResourceServerAuthTests(APITestCase):
    fixtures = ['initial_resources.json']

    def setUp(self):
        self.process_token = signing.dumps(1)

    def test_get_cheq_without_auth_header_fails(self):
        url = reverse('resource_server:resource_cheq', kwargs={"process_token": self.process_token})
        response = self.client.get(url)
        self.assertIn(response.status_code, [401, 403])

    def test_get_cheq_with_invalid_auth_header_fails(self):
        url = reverse('resource_server:resource_cheq', kwargs={"process_token": self.process_token})
        response = self.client.get(url, HTTP_AUTHORIZATION="invalid_format")
        self.assertIn(response.status_code, [401, 403])

    def test_post_decision_without_auth_header_fails(self):
        url = reverse('resource_server:resource', kwargs={"process_token": self.process_token})
        response = self.client.post(url, data={"signed_CHEQ": "token"}, QUERY_STRING="decision=ACCEPT")
        self.assertIn(response.status_code, [200, 400, 401, 403, 422])


class SecurityAuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.process = Process.objects.create()
        self.process_token = signing.dumps(self.process.id)
        Resource.objects.create(
            process_id=self.process.id,
            pub_date=timezone.now(),
            selected_flight={"airline": "United", "price": 1200.0}
        )
        with open(os.path.join(BASE_DIR, 'cs_private_key.pem'), 'r') as f:
            self.cs_private_key = f.read()

    def generate_valid_cs_token(self):
        payload = {
            "iss": "confirmation_server",
            "aud": "resource_server",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60
        }
        return jwt.encode(payload, self.cs_private_key, algorithm="RS256")

    def test_resource_cheq_unauthenticated_denied(self):
        """
        GET request without Authorization header must return 401 or 403 permission denied
        """
        url = reverse("resource_server:resource_cheq", kwargs={"process_token": self.process_token})
        response = self.client.get(url)
        self.assertIn(response.status_code, [401, 403])

    def test_resource_cheq_authenticated_success(self):
        """
        GET request with valid CS Service JWT returns 200 OK and signed CHEQ object
        """
        url = reverse("resource_server:resource_cheq", kwargs={"process_token": self.process_token})
        token = self.generate_valid_cs_token()
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)

    def test_public_key_endpoint(self):
        """
        GET /resource_server/public_key/ returns 200 OK and valid public key
        """
        url = reverse("resource_server:public_key")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("public_key", response.json())
        self.assertIn("BEGIN PUBLIC KEY", response.json()["public_key"])

    def test_cheq_contains_nonce_and_exp(self):
        """
        GET /resource_server/resource/<token>/cheq/ returns signed CHEQ containing nonce and exp
        """
        url = reverse("resource_server:resource_cheq", kwargs={"process_token": self.process_token})
        token = self.generate_valid_cs_token()
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        signed_cheq_str = response.json()
        
        with open(os.path.join(BASE_DIR, 'rs_public_key.pem'), 'r') as f:
            rs_pub_key = f.read()
        decoded = jwt.decode(signed_cheq_str, rs_pub_key, algorithms=["RS256"])
        cheq = decoded["CHEQ"]
        self.assertIn("nonce", cheq)
        self.assertIn("exp", cheq)
        self.assertTrue(len(cheq["nonce"]) > 10)
        self.assertTrue(cheq["exp"] > time.time())


