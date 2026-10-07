from rest_framework.views import APIView
from rest_framework.response import Response
from core.crypto import get_cs_key_manager

class ConfirmationServerJWKSView(APIView):
    """
    Exposes RFC 7517 JSON Web Key Set for the Confirmation Server.
    Provides public keys for JWS verification (sig) and JWE encryption (enc).
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        km = get_cs_key_manager()
        jwks = km.get_public_jwks()
        response = Response(jwks, status=200)
        response["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
        response["Content-Type"] = "application/json; charset=utf-8"
        return response
