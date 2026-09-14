import os
import time
import jwt
import requests
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

# BASE_DIR points to the project root where the .pem key files are stored
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_jwks_cache = {
    "keys": [],
    "fetched_at": 0
}

def get_jwks():
    global _jwks_cache
    now = time.time()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < 3600:
        return _jwks_cache["keys"]

    domain = getattr(settings, 'AUTH0_DOMAIN', None)
    if not domain:
        raise ValueError("Missing AUTH0_DOMAIN setting.")

    jwks_url = f"https://{domain}/.well-known/jwks.json"
    response = requests.get(jwks_url)
    response.raise_for_status()
    jwks = response.json()
    _jwks_cache["keys"] = jwks.get("keys", [])
    _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]

class SignatureService:
    @staticmethod
    def verify_auth0_token(token):
        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            raise AuthenticationFailed("Invalid token header.")

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthenticationFailed("Token header is missing 'kid'.")

        try:
            keys = get_jwks()
        except Exception as e:
            raise AuthenticationFailed(f"Failed to retrieve JWKS: {e}")

        rsa_key = {}
        for key in keys:
            if key["kid"] == kid:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }
                break

        if not rsa_key:
            raise AuthenticationFailed("Unable to find appropriate key in JWKS.")

        try:
            from jwt.algorithms import RSAAlgorithm
            public_key = RSAAlgorithm.from_jwk(rsa_key)
        except Exception as e:
            raise AuthenticationFailed(f"Failed to parse public key from JWK: {e}")

        domain = getattr(settings, 'AUTH0_DOMAIN', None)
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

    def sign(self, CHEQ):
        try:

            with open(os.path.join(BASE_DIR, 'rs_private_key.pem'), 'r') as f:
                signing_key = f.read()
            encoded_jwt = jwt.encode({"CHEQ": CHEQ}, signing_key, algorithm="RS256")
            return encoded_jwt
        except Exception as e:
            raise e

    def verify(self, CHEQ):
        try:

            with open(os.path.join(BASE_DIR, 'cs_public_key.pem'), 'r') as f:
                verification_key = f.read()
            decoded_jwt = jwt.decode(CHEQ, verification_key, algorithms=["RS256"], verify_signature=True, require=["CHEQ"])
            return decoded_jwt
        except Exception as e:
            raise e