import os
import time
import jwt
import requests
from typing import Optional, Dict, Any
from django.conf import settings
from core.crypto import (
    get_cs_key_manager,
    get_jwks_cache,
    NestedTokenService,
    TokenValidationError
)

_token_cache = {
    "access_token": None,
    "expires_at": 0
}

def get_access_token():
    global _token_cache
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]

    domain = getattr(settings, 'AUTH0_DOMAIN', None)
    client_id = getattr(settings, 'AUTH0_CLIENT_ID', None)
    client_secret = getattr(settings, 'AUTH0_CLIENT_SECRET', None)
    audience = getattr(settings, 'AUTH0_AUDIENCE', None)

    if not all([domain, client_id, client_secret, audience]):
        raise ValueError("Missing Auth0 configurations in settings/environment variables.")

    url = f"https://{domain}/oauth/token"
    headers = {"content-type": "application/json"}
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "audience": audience,
        "grant_type": "client_credentials"
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()

    access_token = data["access_token"]
    expires_in = data.get("expires_in", 86400)

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in

    return access_token

def derive_rs_jwks_url(resource_uri: str) -> str:
    if "/resource/" in resource_uri:
        base = resource_uri.split("/resource/")[0].rstrip("/")
    else:
        # Fallback if no trailing slash in resource segment
        idx = resource_uri.rfind("/resource")
        base = resource_uri[:idx].rstrip("/") if idx != -1 else resource_uri.rstrip("/")
    return f"{base}/.well-known/jwks.json"

class ConfirmationService:
    @staticmethod
    def generate_service_jwt():
        cs_km = get_cs_key_manager()
        signing_key = cs_km.export_signing_private_pem()
        payload = {
            "iss": "confirmation_server",
            "aud": "resource_server",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60
        }
        headers = {"kid": cs_km.sig_kid}
        return jwt.encode(payload, signing_key, algorithm="RS256", headers=headers)

    @classmethod
    def retrieveCHEQ(cls, resource_uri: str, rs_jwks_url: Optional[str] = None) -> dict:
        try:
            cheq_endpoint = f"{resource_uri.rstrip('/')}/cheq/"
            service_token = cls.generate_service_jwt()
            headers = {
                "Authorization": f"Bearer {service_token}",
                "Accept": "application/jose+json, application/json"
            }
            response = requests.get(cheq_endpoint, headers=headers)
            if response.status_code == 404:
                raise IndexError
            if response.status_code != 200:
                raise Exception(f"Resource Server returned status {response.status_code}: {response.text}")

            if response.headers.get("content-type", "").startswith("application/json"):
                try:
                    CHEQ = response.json()
                except Exception:
                    CHEQ = response.text.strip().strip('"')
            else:
                CHEQ = response.text.strip().strip('"')

            parts = CHEQ.strip().split(".")
            if not rs_jwks_url:
                rs_jwks_url = derive_rs_jwks_url(resource_uri)

            cs_km = get_cs_key_manager()
            cache = get_jwks_cache()

            if len(parts) == 5:
                # Nested JWE token - decrypt using CS KeyManager (supports active & retired keys)
                verified_CHEQ = NestedTokenService.decrypt_and_verify_nested_token(
                    token_string=CHEQ,
                    recipient_enc_key=cs_km,
                    jwks_cache=cache,
                    sender_jwks_url=rs_jwks_url,
                    expected_issuer="resource_server",
                    expected_audience="confirmation_server"
                )
            elif len(parts) == 3:
                # Legacy JWS token - verify RS signature using JWKSCache
                unverified_header = jwt.get_unverified_header(CHEQ)
                kid = unverified_header.get("kid")
                rs_pub_key = cache.get_key(rs_jwks_url, kid=kid, use="sig")
                verification_pem = rs_pub_key.export_to_pem(private_key=False).decode("utf-8")
                verified_CHEQ = jwt.decode(CHEQ, verification_pem, algorithms=["RS256"], verify_signature=True, require=["CHEQ"])
            else:
                raise ValueError("Invalid token format received from Resource Server.")

        except Exception as e:
            raise e
        return verified_CHEQ

    @classmethod
    def sign(cls, payload: dict, rs_jwks_url: Optional[str] = None, encrypt_for_rs: bool = True) -> str:
        token_payload = payload if (isinstance(payload, dict) and "CHEQ" in payload) else {"CHEQ": payload}
        cs_km = get_cs_key_manager()
        if not encrypt_for_rs:
            signing_pem = cs_km.export_signing_private_pem()
            return jwt.encode(token_payload, signing_pem, algorithm="RS256")

        cache = get_jwks_cache()
        if not rs_jwks_url:
            rs_jwks_url = getattr(
                settings,
                'RS_JWKS_URL',
                'http://127.0.0.1:8000/resource_server/.well-known/jwks.json'
            )
        recipient_enc_key = cache.get_key(rs_jwks_url, use="enc")

        return NestedTokenService.create_nested_token(
            payload=token_payload,
            signer_key=cs_km.signing_key,
            recipient_enc_key=recipient_enc_key,
            issuer="confirmation_server",
            audience="resource_server"
        )

    @classmethod
    def sendDecisionToRS(
        cls,
        CHEQ: dict,
        decision: str,
        resource_uri: str,
        extra_data: Optional[dict] = None,
        auth_token: Optional[str] = None,
        rs_jwks_url: Optional[str] = None
    ) -> requests.Response:
        headers = {}
        if auth_token:
            headers["Authorization"] = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        else:
            try:
                headers["Authorization"] = f"Bearer {get_access_token()}"
            except Exception as e:
                print(f"Warning: Could not obtain M2M access token: {e}")

        if not rs_jwks_url:
            rs_jwks_url = derive_rs_jwks_url(resource_uri)

        # Bind decision cryptographically within the signed & encrypted token payload
        token_payload = {
            "CHEQ": CHEQ,
            "decision": decision
        }
        signed_cheq = cls.sign(token_payload, rs_jwks_url=rs_jwks_url, encrypt_for_rs=True)

        payload = {"signed_CHEQ": signed_cheq}
        if extra_data:
            payload.update(extra_data)

        # Do NOT pass decision in URL query parameters. It is bound inside signed_CHEQ.
        response = requests.post(
            f"{resource_uri.rstrip('/')}/",
            data=payload,
            headers=headers
        )
        return response
