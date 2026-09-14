import os
import time
import jwt
import requests
from django.conf import settings

# BASE_DIR points to the project root where the .pem key files are stored
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

class ConfirmationService:
    @staticmethod
    def generate_service_jwt():
        with open(os.path.join(BASE_DIR, 'cs_private_key.pem'), 'r') as f:
            signing_key = f.read()
        payload = {
            "iss": "confirmation_server",
            "aud": "resource_server",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60
        }
        return jwt.encode(payload, signing_key, algorithm="RS256")

    @classmethod
    def retrieveCHEQ(cls, *args, **kwargs):
        # Support both retrieveCHEQ(resource_uri) and legacy retrieveCHEQ(self, resource_uri)
        resource_uri = kwargs.get("resource_uri") or args[-1]
        try:
            cheq_endpoint = f"{resource_uri.rstrip('/')}/cheq/"
            service_token = cls.generate_service_jwt()
            headers = {"Authorization": f"Bearer {service_token}"}
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

            with open(os.path.join(BASE_DIR, 'rs_public_key.pem'), 'r') as f:
                verification_key = f.read()
            verified_CHEQ = jwt.decode(CHEQ, verification_key, algorithms=["RS256"], verify_signature=True, require=["CHEQ"])
        except Exception as e:
            raise e
        return verified_CHEQ

    @classmethod
    def sign(cls, *args, **kwargs):
        # Support both sign(CHEQ) and legacy sign(self, CHEQ)
        CHEQ = kwargs.get("CHEQ") or args[-1]
        try:
            with open(os.path.join(BASE_DIR, 'cs_private_key.pem'), 'r') as f:
                signing_key = f.read()
            encoded_jwt = jwt.encode({"CHEQ": CHEQ}, signing_key, algorithm="RS256")
            return encoded_jwt
        except Exception as e:
            raise e

    @classmethod
    def sendDecisionToRS(cls, *args, **kwargs):
        # Support both sendDecisionToRS(CHEQ, decision, resource_uri, extra_data=..., auth_token=...)
        # and legacy sendDecisionToRS(self, CHEQ, decision, resource_uri, extra_data=..., auth_token=...)
        if len(args) >= 4 and not isinstance(args[0], (dict, str)):
            CHEQ, decision, resource_uri = args[1], args[2], args[3]
        elif len(args) >= 3:
            CHEQ, decision, resource_uri = args[0], args[1], args[2]
        else:
            CHEQ = kwargs.get("CHEQ") or args[0]
            decision = kwargs.get("decision") or args[1]
            resource_uri = kwargs.get("resource_uri") or args[2]

        extra_data = kwargs.get("extra_data")
        auth_token = kwargs.get("auth_token")

        headers = {}
        if auth_token:
            headers["Authorization"] = auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"
        else:
            try:
                headers["Authorization"] = f"Bearer {get_access_token()}"
            except Exception as e:
                print(f"Warning: Could not obtain M2M access token: {e}")

        signed_cheq = cls.sign(CHEQ)
        payload = {"signed_CHEQ": signed_cheq}
        if extra_data:
            payload.update(extra_data)
        response = requests.post(f"{resource_uri.rstrip('/')}/",
                                 data=payload,
                                 params={"decision": decision},
                                 headers=headers)
        return response
