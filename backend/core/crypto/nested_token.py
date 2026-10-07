import time
import json
import uuid
import logging
from typing import Optional, Dict, Any
from jwcrypto import jwk, jws, jwe

logger = logging.getLogger(__name__)

ALLOWED_JWS_ALGS = ["RS256", "ES256"]
ALLOWED_JWE_ALGS = ["RSA-OAEP-256"]
ALLOWED_JWE_ENCS = ["A256GCM"]

class TokenValidationError(Exception):
    """Raised when token decryption or validation fails."""
    pass

class NestedTokenService:
    """
    Implements RFC 7515 (JWS) nested within RFC 7516 (JWE) - "Sign-then-Encrypt".
    
    Provides non-repudiation, tamper-evidence, and payload confidentiality.
    """
    @staticmethod
    def create_nested_token(
        payload: Dict[str, Any],
        signer_key: jwk.JWK,
        recipient_enc_key: jwk.JWK,
        signer_kid: Optional[str] = None,
        recipient_kid: Optional[str] = None,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        expires_in: int = 60
    ) -> str:
        now = int(time.time())
        token_payload = dict(payload)

        # Standard RFC 7519 Claims
        if "iat" not in token_payload:
            token_payload["iat"] = now
        if "nbf" not in token_payload:
            token_payload["nbf"] = now
        if "exp" not in token_payload:
            token_payload["exp"] = now + expires_in
        if "jti" not in token_payload:
            token_payload["jti"] = str(uuid.uuid4())
        if issuer and "iss" not in token_payload:
            token_payload["iss"] = issuer
        if audience and "aud" not in token_payload:
            token_payload["aud"] = audience

        # Phase 1: Digital Signature (JWS)
        jws_protected = {
            "alg": signer_key.get("alg", "RS256"),
            "typ": "JWT",
            "kid": signer_kid or signer_key.key_id
        }
        jws_obj = jws.JWS(json.dumps(token_payload).encode("utf-8"))
        jws_obj.add_signature(signer_key, alg=jws_protected["alg"], protected=jws_protected)
        inner_jws = jws_obj.serialize(compact=True)

        # Phase 2: Key Wrap & Payload Encryption (JWE)
        jwe_protected = {
            "alg": recipient_enc_key.get("alg", "RSA-OAEP-256"),
            "enc": "A256GCM",
            "cty": "JWT",
            "kid": recipient_kid or recipient_enc_key.key_id
        }
        jwe_obj = jwe.JWE(
            plaintext=inner_jws.encode("utf-8"),
            recipient=recipient_enc_key,
            protected=jwe_protected
        )
        outer_jwe = jwe_obj.serialize(compact=True)
        return outer_jwe

    @staticmethod
    def decrypt_and_verify_nested_token(
        token_string: str,
        recipient_enc_key: jwk.JWK,
        jwks_cache: Any,
        sender_jwks_url: str,
        expected_issuer: Optional[str] = None,
        expected_audience: Optional[str] = None,
        allowed_clock_skew: int = 5
    ) -> Dict[str, Any]:
        # 1. Decrypt Outer JWE
        try:
            jwe_obj = jwe.JWE()
            jwe_obj.deserialize(token_string)
            jwe_header = jwe_obj.jose_header

            if hasattr(recipient_enc_key, "get_key_by_kid"):
                kid = jwe_header.get("kid")
                key = recipient_enc_key.get_key_by_kid(kid) if kid else None
                if not key:
                    key = recipient_enc_key.encryption_key
            else:
                key = recipient_enc_key

            jwe_obj.decrypt(key)
            inner_jws_string = jwe_obj.payload.decode("utf-8")
        except Exception as e:
            raise TokenValidationError(f"JWE decryption failed: {e}")

        # Validate JWE Header properties
        jwe_header = jwe_obj.jose_header
        if jwe_header.get("alg") not in ALLOWED_JWE_ALGS:
            raise TokenValidationError(f"Disallowed JWE algorithm: {jwe_header.get('alg')}")
        if jwe_header.get("enc") not in ALLOWED_JWE_ENCS:
            raise TokenValidationError(f"Disallowed JWE content encryption: {jwe_header.get('enc')}")

        # 2. Parse and Verify Inner JWS
        try:
            jws_obj = jws.JWS()
            jws_obj.deserialize(inner_jws_string)
        except Exception as e:
            raise TokenValidationError(f"Inner JWS deserialization failed: {e}")

        jws_header = jws_obj.jose_header
        alg = jws_header.get("alg")
        if alg not in ALLOWED_JWS_ALGS:
            raise TokenValidationError(f"Disallowed JWS algorithm: {alg}")

        kid = jws_header.get("kid")
        if not kid:
            raise TokenValidationError("Inner JWS header missing 'kid'")

        # Fetch sender's public signing key from JWKS Cache
        try:
            sender_pub_key = jwks_cache.get_key(sender_jwks_url, kid=kid, use="sig")
        except Exception as e:
            raise TokenValidationError(f"Failed to obtain sender public key for kid '{kid}': {e}")

        try:
            jws_obj.verify(sender_pub_key)
            payload_bytes = jws_obj.payload
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            raise TokenValidationError(f"JWS signature verification failed: {e}")

        # 3. Validate Claims
        now = time.time()
        exp = payload.get("exp")
        if exp is not None:
            if now > exp + allowed_clock_skew:
                raise TokenValidationError(f"Token has expired (exp: {exp}, now: {now})")

        nbf = payload.get("nbf")
        if nbf is not None:
            if now < nbf - allowed_clock_skew:
                raise TokenValidationError(f"Token not yet valid (nbf: {nbf}, now: {now})")

        if expected_issuer:
            iss = payload.get("iss")
            if iss != expected_issuer:
                raise TokenValidationError(f"Invalid issuer: expected '{expected_issuer}', got '{iss}'")

        if expected_audience:
            aud = payload.get("aud")
            if aud != expected_audience:
                raise TokenValidationError(f"Invalid audience: expected '{expected_audience}', got '{aud}'")

        return payload
