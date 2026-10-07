import json
import jwt
from typing import Optional, Dict, Any
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from core.crypto import (
    get_rs_key_manager,
    get_jwks_cache,
    NestedTokenService,
)

class SignatureService:
    @staticmethod
    def verify_auth0_token(token: str) -> Dict[str, Any]:
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            raise AuthenticationFailed("Invalid token header.")

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthenticationFailed("Token header is missing 'kid'.")

        domain = getattr(settings, 'AUTH0_DOMAIN', None)
        if not domain:
            raise ValueError("Missing AUTH0_DOMAIN setting.")

        jwks_url = f"https://{domain}/.well-known/jwks.json"
        cache = get_jwks_cache()

        try:
            rsa_key = cache.get_key(jwks_url, kid=kid, use="sig")
        except Exception as e:
            raise AuthenticationFailed(f"Failed to retrieve Auth0 signing key: {e}")

        try:
            from jwt.algorithms import RSAAlgorithm
            public_key = RSAAlgorithm.from_jwk(json.loads(rsa_key.export_public()))
        except Exception as e:
            raise AuthenticationFailed(f"Failed to parse public key from JWK: {e}")

        audience = getattr(settings, 'AUTH0_AUDIENCE', None)

        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=audience,
                issuer=f"https://{domain}/",
                options={"verify_signature": True}
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Token has expired.")
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed(f"Invalid token: {e}")

    @classmethod
    def sign(cls, CHEQ: Dict[str, Any], encrypt_for_cs: bool = False, cs_jwks_url: Optional[str] = None) -> str:
        """
        Signs the CHEQ using RS private signing key.
        If encrypt_for_cs is True, encapsulates in JWE encrypted with CS public encryption key
        fetched over HTTP via JWKSCache (Nested JWS-in-JWE / Sign-then-Encrypt).
        """
        try:
            rs_km = get_rs_key_manager()
            if encrypt_for_cs:
                if not cs_jwks_url:
                    cs_jwks_url = getattr(
                        settings,
                        'CONFIRMATION_SERVER_JWKS_URL',
                        'http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json'
                    )
                cache = get_jwks_cache()
                cs_enc_key = cache.get_key(cs_jwks_url, use="enc")

                return NestedTokenService.create_nested_token(
                    payload={"CHEQ": CHEQ},
                    signer_key=rs_km.signing_key,
                    recipient_enc_key=cs_enc_key,
                    issuer="resource_server",
                    audience="confirmation_server"
                )
            else:
                signing_pem = rs_km.export_signing_private_pem()
                return jwt.encode({"CHEQ": CHEQ}, signing_pem, algorithm="RS256")
        except Exception as e:
            raise e

    @classmethod
    def verify(cls, CHEQ_token: str, cs_jwks_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Verifies and decrypts CHEQ token received from Confirmation Server.
        Retrieves CS public signing key strictly via JWKSCache (No in-process shortcuts, no disk PEMs).
        """
        try:
            parts = CHEQ_token.strip().split(".")
            if not cs_jwks_url:
                cs_jwks_url = getattr(
                    settings,
                    'CONFIRMATION_SERVER_JWKS_URL',
                    'http://127.0.0.1:8000/confirmation_server/.well-known/jwks.json'
                )

            rs_km = get_rs_key_manager()
            cache = get_jwks_cache()

            if len(parts) == 5:
                # 5-part Nested JWE token - decrypt with RS key manager, verify CS signature via JWKSCache
                return NestedTokenService.decrypt_and_verify_nested_token(
                    token_string=CHEQ_token,
                    recipient_enc_key=rs_km,
                    jwks_cache=cache,
                    sender_jwks_url=cs_jwks_url,
                    expected_issuer="confirmation_server",
                    expected_audience="resource_server"
                )
            elif len(parts) == 3:
                # 3-part Legacy JWS token - verify CS signature using JWKSCache
                unverified_header = jwt.get_unverified_header(CHEQ_token)
                kid = unverified_header.get("kid")
                cs_pub_key = cache.get_key(cs_jwks_url, kid=kid, use="sig")
                verification_pem = cs_pub_key.export_to_pem(private_key=False).decode("utf-8")
                return jwt.decode(CHEQ_token, verification_pem, algorithms=["RS256"], verify_signature=True)
            else:
                raise ValueError("Invalid token format: expected 3 or 5 compact segments.")
        except Exception as e:
            raise e