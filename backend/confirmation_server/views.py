from wsgiref.validate import validator

from django.views import generic
from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.permissions import IsAuthenticated
from .auth import Auth0JWTAuthentication

from .serializers import ResourceUriCheqMappingSerializer
from .services import ConfirmationService
from enum import Enum
from .models import ResourceUriCheqMapping
import traceback
valid_decisions = ["ACCEPT", "REJECT"]
host = "http://127.0.0.1:8000"

class IndexView(generic.ListView):
    template_name = "confirmation_server/trigger_confirmation.html"
    context_object_name = "confirmation_server_landing"

    def get_queryset(self):
        """
        Return the last five published questions (not including those set to be
        published in the future).
        """
        return True

class TriggerView(APIView):
    authentication_classes = [Auth0JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data

        if type(data) is not dict:
            return Response(status=415)
        if len(data.keys()) != 1:
            return Response(status=422)
        if "resource_uri" not in data.keys():
            return Response(status=422)
        resource_uri_validator = URLValidator(schemes=['http', 'https'])
        try:
            resource_uri = data['resource_uri']
            resource_uri_validator(resource_uri)
        except ValidationError as e:
            print(f"Invalid resource URI: {e}")
            return Response(status=422)

        try:
            response = ConfirmationService.retrieveCHEQ(resource_uri)
            ResourceUriCheqMapping.objects.filter(resource_uri=resource_uri).delete()
            mapping = ResourceUriCheqMapping(
                resource_uri= resource_uri,
                CHEQ= response
            )
            mapping.save()
            perform_confirmation_uri = host + reverse("confirmation_server:perform")
            response["perform_confirmation_uri"] = perform_confirmation_uri
            return Response(response, status=200)
        except IndexError: #this gets raised by the confirmation if the resource server responds with 404 when looking for a CHEQ
            return Response(status=404)
        except Exception as e:
            print(f"Error in TriggerView: {e}")

            traceback.print_exc()
            return Response({"error": str(e)}, status=500)
class PerformView(APIView):
    authentication_classes = [Auth0JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data
        #Input validation
        #{"resource_uri": "http://127.0.0.1:8000/resource_server/resource/1/", "decision": "accept"}
        if type(data) is not dict:
            return Response(status=415)
        if "resource_uri" not in data or "decision" not in data:
            return Response("Request body must contain resource_uri and decision clauses", status=422)

        resource_uri_validator = URLValidator(schemes=['http', 'https'])
        try:
            resource_uri = data['resource_uri']
            resource_uri_validator(resource_uri)
        except ValidationError as e:
            print(f"Invalid resource URI: {e}")
            return Response("Invalid resource URI", status=422)

        if data["decision"] not in valid_decisions:
            print(f"Invalid decision")
            return Response("Invalid decision", status=422)
        else:
            decision = data["decision"]

        # Extract extra fields (secure direct inputs)
        extra_data = {k: v for k, v in data.items() if k not in ["resource_uri", "decision"]}

        auth_token = request.headers.get("Authorization")

        #get cheq object from DB, and send to RS along with decision
        try:
            CHEQ_query = ResourceUriCheqMapping.objects.filter(resource_uri=resource_uri).order_by('-id')
            CHEQ_serializer = ResourceUriCheqMappingSerializer(CHEQ_query, many=True)

            if CHEQ_serializer.data:
                CHEQ = CHEQ_serializer.data[0]['CHEQ']['CHEQ']
                response = ConfirmationService.sendDecisionToRS(
                    CHEQ, decision, resource_uri, extra_data=extra_data, auth_token=auth_token
                )
                if response.status_code == 200:
                    return Response("Decision sent", status=200)
                else:
                    return Response(response.text, status=400)
            else:
                return Response("No CHEQ object registered for this resource URI", status=400)
        except Exception as e:
            return Response("Error processing decision: " + str(e), status=400)






