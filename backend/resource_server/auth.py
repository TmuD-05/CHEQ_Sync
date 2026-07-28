import os
import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class ConfirmationServerUser:
    def __init__(self, token_payload):
        self.payload = token_payload
        self.username = token_payload.get("iss", "confirmation_server")
        self.is_authenticated = True

    def __str__(self):
        return self.username

    def is_anonymous(self):
        return False

    def is_active(self):
        return True

class ConfirmationServerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        parts = auth_header.split()
        if parts[0].lower() != "bearer":
            return None

        if len(parts) == 1:
            raise AuthenticationFailed("Invalid token header. No credentials provided.")
        elif len(parts) > 2:
            raise AuthenticationFailed("Invalid token header. Token string should not contain spaces.")

        token = parts[1]

        try:
            cs_public_key_path = os.path.join(BASE_DIR, 'cs_public_key.pem')
            with open(cs_public_key_path, 'r') as f:
                verification_key = f.read()

            payload = jwt.decode(
                token,
                verification_key,
                algorithms=["RS256"],
                audience="resource_server",
                issuer="confirmation_server"
            )

            user = ConfirmationServerUser(payload)
            return (user, token)

        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Confirmation Server service token has expired.")
        except jwt.InvalidSignatureError:
            raise AuthenticationFailed("Confirmation Server service token signature is invalid.")
        except jwt.DecodeError:
            raise AuthenticationFailed("Confirmation Server service token decoding failed.")
        except Exception as e:
            raise AuthenticationFailed(f"Confirmation Server authentication failed: {str(e)}")
