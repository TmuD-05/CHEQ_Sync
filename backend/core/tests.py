import time
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from core.crypto.key_manager import KeyManager
from core.crypto.jwks_cache import JWKSCache, KeyNotFoundError, RateLimitError
from core.crypto.nested_token import NestedTokenService, TokenValidationError

class KeyManagerTests(TestCase):
    def test_key_manager_generates_keys_in_memory(self):
        km = KeyManager(service_name="test_service", key_size=2048)
        self.assertIsNotNone(km.signing_key)
        self.assertIsNotNone(km.encryption_key)

        jwks = km.get_public_jwks()
        self.assertIn("keys", jwks)
        self.assertEqual(len(jwks["keys"]), 2)

        sig_jwk = next(k for k in jwks["keys"] if k.get("use") == "sig")
        enc_jwk = next(k for k in jwks["keys"] if k.get("use") == "enc")

        self.assertEqual(sig_jwk["kty"], "RSA")
        self.assertEqual(sig_jwk["alg"], "RS256")
        self.assertNotIn("d", sig_jwk)  # Must be public only

        self.assertEqual(enc_jwk["kty"], "RSA")
        self.assertEqual(enc_jwk["alg"], "RSA-OAEP-256")
        self.assertNotIn("d", enc_jwk)  # Must be public only

    def test_key_manager_signing_key_rotation(self):
        km = KeyManager(service_name="test_service")
        old_kid = km.sig_kid

        new_kid = km.rotate_signing_key(grace_period_seconds=300)
        self.assertNotEqual(old_kid, new_kid)
        self.assertEqual(km.sig_kid, new_kid)

        jwks = km.get_public_jwks()
        self.assertEqual(len(jwks["keys"]), 3)  # Active sig, active enc, retired sig
        kids = [k["kid"] for k in jwks["keys"]]
        self.assertIn(old_kid, kids)
        self.assertIn(new_kid, kids)

    def test_key_manager_encryption_key_rotation(self):
        km = KeyManager(service_name="test_service")
        old_kid = km.enc_kid

        new_kid = km.rotate_encryption_key(grace_period_seconds=300)
        self.assertNotEqual(old_kid, new_kid)
        self.assertEqual(km.enc_kid, new_kid)

        jwks = km.get_public_jwks()
        self.assertEqual(len(jwks["keys"]), 3)  # Active sig, active enc, retired enc
        kids = [k["kid"] for k in jwks["keys"]]
        self.assertIn(old_kid, kids)
        self.assertIn(new_kid, kids)

    def test_key_manager_expired_retired_keys_purged(self):
        km = KeyManager(service_name="test_service")
        km.rotate_signing_key(grace_period_seconds=-1)  # Immediately expired

        jwks = km.get_public_jwks()
        self.assertEqual(len(jwks["keys"]), 2)  # Retired key should be purged

class JWKSCacheTests(TestCase):
    def setUp(self):
        self.cache = JWKSCache(cache_ttl_seconds=3600, cooldown_seconds=60)
        self.km = KeyManager("test_remote")
        self.jwks = self.km.get_public_jwks()
        self.jwks_url = "https://example.com/.well-known/jwks.json"

    def test_registered_key_retrieval(self):
        self.cache.register_jwks(self.jwks_url, self.jwks)
        key = self.cache.get_key(self.jwks_url, kid=self.km.sig_kid, use="sig")
        self.assertIsNotNone(key)
        self.assertEqual(key.key_id, self.km.sig_kid)

    @patch("core.crypto.jwks_cache.requests.get")
    def test_remote_fetch_and_anti_dos_throttling(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = self.jwks
        mock_get.return_value = mock_response

        # First fetch succeeds
        key = self.cache.get_key(self.jwks_url, kid=self.km.sig_kid)
        self.assertEqual(key.key_id, self.km.sig_kid)
        self.assertEqual(mock_get.call_count, 1)

        # Cache hit - does not call remote
        key2 = self.cache.get_key(self.jwks_url, kid=self.km.sig_kid)
        self.assertEqual(key2.key_id, self.km.sig_kid)
        self.assertEqual(mock_get.call_count, 1)

        # Unknown kid within cooldown period raises RateLimitError (Anti-DoS)
        with self.assertRaises(RateLimitError):
            self.cache.get_key(self.jwks_url, kid="unknown-kid-attempt")

    def test_get_key_requires_kid_or_use(self):
        with self.assertRaises(ValueError):
            self.cache.get_key(self.jwks_url)

class NestedTokenServiceTests(TestCase):
    def setUp(self):
        self.rs_km = KeyManager("rs_test")
        self.cs_km = KeyManager("cs_test")

        self.cache = JWKSCache()
        self.rs_jwks_url = "https://resource-server.internal/.well-known/jwks.json"
        self.cs_jwks_url = "https://confirmation-server.internal/.well-known/jwks.json"

        self.cache.register_jwks(self.rs_jwks_url, self.rs_km.get_public_jwks())
        self.cache.register_jwks(self.cs_jwks_url, self.cs_km.get_public_jwks())

    def test_nested_token_sign_then_encrypt_success(self):
        payload = {
            "CHEQ": {"flight_id": "UA123", "price": 1200}
        }

        # RS signs with its private signing key, encrypts with CS public encryption key
        token = NestedTokenService.create_nested_token(
            payload=payload,
            signer_key=self.rs_km.signing_key,
            recipient_enc_key=self.cs_km.encryption_key,
            issuer="resource_server",
            audience="confirmation_server"
        )
        self.assertIsInstance(token, str)
        # JWE compact format has 5 parts separated by dots
        self.assertEqual(len(token.split(".")), 5)

        # CS decrypts with its private encryption key, verifies signature with RS public key from cache
        recovered_payload = NestedTokenService.decrypt_and_verify_nested_token(
            token_string=token,
            recipient_enc_key=self.cs_km.encryption_key,
            jwks_cache=self.cache,
            sender_jwks_url=self.rs_jwks_url,
            expected_issuer="resource_server",
            expected_audience="confirmation_server"
        )

        self.assertEqual(recovered_payload["CHEQ"]["flight_id"], "UA123")
        self.assertEqual(recovered_payload["iss"], "resource_server")
        self.assertEqual(recovered_payload["aud"], "confirmation_server")

    def test_tampered_jwe_fails_decryption(self):
        token = NestedTokenService.create_nested_token(
            payload={"test": 1},
            signer_key=self.rs_km.signing_key,
            recipient_enc_key=self.cs_km.encryption_key
        )
        # Corrupt the ciphertext part of JWE
        parts = token.split(".")
        corrupted_ciphertext = parts[3][:-4] + "AAAA"
        corrupted_token = ".".join([parts[0], parts[1], parts[2], corrupted_ciphertext, parts[4]])

        with self.assertRaises(TokenValidationError):
            NestedTokenService.decrypt_and_verify_nested_token(
                token_string=corrupted_token,
                recipient_enc_key=self.cs_km.encryption_key,
                jwks_cache=self.cache,
                sender_jwks_url=self.rs_jwks_url
            )

    def test_expired_token_rejected(self):
        token = NestedTokenService.create_nested_token(
            payload={"test": 1},
            signer_key=self.rs_km.signing_key,
            recipient_enc_key=self.cs_km.encryption_key,
            expires_in=-10  # Expired 10 seconds ago
        )
        with self.assertRaises(TokenValidationError) as ctx:
            NestedTokenService.decrypt_and_verify_nested_token(
                token_string=token,
                recipient_enc_key=self.cs_km.encryption_key,
                jwks_cache=self.cache,
                sender_jwks_url=self.rs_jwks_url,
                allowed_clock_skew=0
            )
        self.assertIn("expired", str(ctx.exception).lower())
