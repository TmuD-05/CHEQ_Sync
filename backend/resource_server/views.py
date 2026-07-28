import uuid
import re
import os
import requests
# pyrefly: ignore [missing-import]
from django.http.request import QueryDict
from django.views import generic
from django.urls import reverse
from django.core import signing
from django.core.exceptions import ValidationError
from .models import Resource, ResourceToConfirmationMapping, Result, Process, Flight
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from .serializers import ResourceSerializer, ResourceToConfirmationMappingSerializer, ResultSerializer
from datetime import datetime
from .services import SignatureService
from .auth import ConfirmationServerAuthentication
from django.utils import timezone

host = "http://127.0.0.1:8000"
valid_decisions = ["ACCEPT", "REJECT"]

def get_process_id_from_token(process_token):
    try:
        return signing.loads(process_token)
    except signing.BadSignature:
        raise ValidationError("Invalid or tampered process token")

def parse_flight_number(selected_flight_str):
    if not selected_flight_str:
        return ""
    # Matches a standard airline code and flight number (e.g. AC3 or WS110 or NH135)
    match = re.search(r'\b([a-zA-Z]{2,3}\d{1,4})\b', selected_flight_str)
    if match:
        return match.group(1).upper()
    return selected_flight_str.strip()
def check_auth0_token(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        raise AuthenticationFailed("Authorization header is missing.")
    parts = auth_header.split()
    if parts[0].lower() != 'bearer':
        raise AuthenticationFailed("Authorization header must start with Bearer.")
    elif len(parts) == 1:
        raise AuthenticationFailed("Token not found.")
    elif len(parts) > 2:
        raise AuthenticationFailed("Authorization header must be Bearer token.")

    token = parts[1]
    return SignatureService.verify_auth0_token(token)
class IndexView(generic.ListView):
    template_name = "resource_server/index.html"
    context_object_name = "resource_list"

    def get_queryset(self):
        """
        Return the object within a business process, ordered by sequence number
        """
        return Resource.objects.order_by("process_id")

    def get_context_data(self, *args, **kwargs):
        context = super(IndexView, self).get_context_data(*args, **kwargs)
        context['result_list'] = Result.objects.order_by('process_id')
        return context

class ResourceView(APIView):
    def get(self, request, process_token):
        try:
            process_id = get_process_id_from_token(process_token)
            resources = Resource.objects.filter(process_id=process_id)
            serializer = ResourceSerializer(resources, many=True)
            return Response(serializer.data, status=200)
        except ValidationError as e:
            return Response(str(e), status=422)
        except Exception as e:
            return Response(str(e), status=500)

    def post(self, request, process_token):
        try:
            check_auth0_token(request)
        except AuthenticationFailed as e:
            return Response({"detail": str(e)}, status=401)
        data = request.data
        if request.query_params["decision"] not in valid_decisions:
            print(f"Invalid decision")
            return Response("Invalid decision", status=422)
        else:
            decision = request.query_params["decision"]

        try:
            process_id = get_process_id_from_token(process_token)
        except ValidationError as e:
            return Response(str(e), status=422)

        if type(data) is not QueryDict:
            return Response(status=415)
        if "signed_CHEQ" not in data.keys():
            return Response(status=422)

        CHEQ = data["signed_CHEQ"]
        try:
            verifed_cheq = SignatureService.verify(self, CHEQ)
        except Exception as e:
            return Response(status=400)
        signed_process_id = verifed_cheq['CHEQ']["operation name"]
        if process_id == signed_process_id:
            try:
                result = Result.objects.filter(process_id=signed_process_id).first()
                if result.confirmation_status == "PENDING":
                    result.confirmation_status = decision
                    result.save(update_fields=['confirmation_status'])
                    return Response(status=200)
                else:
                    return Response("Decision for this process already submitted", status=400)
            except Exception as e:
                raise e
        return Response("Process id mismatch", status=400)


class SelectFlightView(APIView):
    def post(self, request, process_token):
        try:
            process_id = get_process_id_from_token(process_token)
        except ValidationError as e:
            return Response(str(e), status=422)
        
        selected_flight = request.data.get("selected_flight")
        if not selected_flight:
            return Response("No flight selection details provided", status=400)

        try:
            resources = Resource.objects.filter(process_id=process_id)
            if not resources.exists():
                return Response(f"No resource found for process {process_id}", status=404)
            
            flight_number = parse_flight_number(selected_flight)
            flight_obj = Flight.objects.filter(process_id=process_id, flight_number__iexact=flight_number).first()
            if not flight_obj:
                flight_obj = Flight.objects.filter(flight_number__iexact=flight_number).first()

            if flight_obj:
                flight_data = {
                    "airline": flight_obj.airline,
                    "flight_number": flight_obj.flight_number,
                    "price": float(flight_obj.price),
                    "origin": flight_obj.origin,
                    "destination": flight_obj.destination,
                    "outbound_date": str(flight_obj.outbound_date),
                    "return_date": str(flight_obj.return_date) if flight_obj.return_date else None,
                    "departure_time": str(flight_obj.departure_time),
                    "arrival_time": str(flight_obj.arrival_time),
                    "duration_minutes": flight_obj.duration_minutes,
                    "stops": flight_obj.stops,
                    "airplane": flight_obj.airplane
                }
            else:
                price_match = re.search(r'\$\s*([\d,]+)', selected_flight)
                price = float(price_match.group(1).replace(",", "")) if price_match else 1788.0
                flight_data = {
                    "airline": "Airline",
                    "flight_number": flight_number or "FL100",
                    "price": price,
                    "origin": "YVR",
                    "destination": "NRT",
                    "outbound_date": "2026-06-25",
                    "return_date": None,
                    "departure_time": "12:30",
                    "arrival_time": "14:40",
                    "duration_minutes": 585,
                    "stops": 0,
                    "airplane": "Boeing 787"
                }

            for resource in resources:
                resource.selected_flight = flight_data
                resource.save(update_fields=["selected_flight"])
            
            return Response("Flight selection saved successfully", status=200)
        except Exception as e:
            return Response(str(e), status=500)


class ResourceCHEQView(APIView):
    authentication_classes = [ConfirmationServerAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, process_token):
        try:
            process_id = get_process_id_from_token(process_token)
        except ValidationError as e:
            return Response(str(e), status=422)

        if process_id:
            resources = Resource.objects.filter(process_id=process_id)
            if (resources.values()):
                serializer = ResourceSerializer(resources, many=True)

                resource_execution_uri = host + reverse("resource_server:resource", kwargs={"process_token": process_token}) + "execute"

                CHEQ = {
                    "version": 1.0,
                    "operation": resource_execution_uri,
                    "operation name": process_id,
                    "inputs" :{
                        "parameters" : serializer.data
                    },
                    "date": datetime.now().strftime("%Y-%m-%d, %H:%M:%S.%f")
                }
                try:
                    signed_CHEQ = SignatureService.sign(self, CHEQ)
                except Exception:
                    return Response(status=422)
                return Response(signed_CHEQ, status=200)
            else:
                return Response(status=404)
        else:
            return Response(status=422)


class PublicKeyView(APIView):
    def get(self, request):
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rs_public_key_path = os.path.join(base_dir, 'rs_public_key.pem')
            with open(rs_public_key_path, 'r') as f:
                pub_key = f.read()
            return Response({"public_key": pub_key}, status=200)
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class ResultView(APIView):
    def get(self, request, process_id):
        if process_id:
            if type(process_id) is int:
                result_query = Result.objects.filter(process_id=process_id)
                serializer = ResultSerializer(result_query, many=True)
                return Response(serializer.data,status=200)
            else:
                return Response(self, status=422)
        else:
            return Response(self, status=500)


class ProcessExecutionWithConfirmation(APIView):
    def post(self, request):
        params = request.data
        response = requests.post("http://127.0.0.1:8000/airline_api/search_flights/", json=params)
        flights = response.json()


        process = Process.objects.create()
        process_id = process.id

        # Save flights to DB for verification page mapping
        best_flights = flights.get("best_flights", [])

        for f in best_flights:
            Flight.objects.create(
                process_id=process_id,
                origin=f["origin"],
                destination=f["destination"],
                outbound_date=f["outbound_date"],
                return_date=f.get("return_date"),
                airline=f["airline"],
                flight_number=f["flight_number"],
                departure_time=f["departure_time"],
                arrival_time=f["arrival_time"],
                duration_minutes=f["duration_minutes"],
                stops=f["stops"],
                price=f["price"],
                airplane=f["airplane"]
            )

        if not ResourceToConfirmationMapping.objects.filter(process_id=process_id).exists():
            ResourceToConfirmationMapping.objects.create(
                process_id=process_id,
                confirmation_uri=f"http://127.0.0.1:8000/confirmation_server/trigger_confirmation/"
            )

        if not Resource.objects.filter(process_id=process_id).exists():
            Resource.objects.create(
                process_id=process_id,
                pub_date = timezone.now(),
            )

        if not Result.objects.filter(process_id=process_id).exists():
            result = Result(process_id=process_id, confirmation_status="PENDING")
            result.save()

        # Obfuscate process_id via secure cryptographic signing
        process_token = signing.dumps(process_id)
        resource_uri = host + reverse("resource_server:resource", kwargs={"process_token": process_token} )
        result_uri = host + reverse("resource_server:result", kwargs={"process_id": process_id} )

        confirmation_query = ResourceToConfirmationMapping.objects.filter(process_id=process_id)
        confirmation_serializer = ResourceToConfirmationMappingSerializer(confirmation_query, many=True)
        confirmation_uri = confirmation_serializer.data[0]["confirmation_uri"]

        response = {
            "resource_uri": resource_uri,
            "confirmation_uri": confirmation_uri,
            "result_uri": result_uri,
            "flights": flights
        }
        return Response(response, status=202)

#TODO:
# implement API endpoint that provides public key for signature verification
# implement confirmation server auth
