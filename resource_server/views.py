from django.http.request import QueryDict
from django.views import generic
from django.urls import reverse
from .models import Resource, ResourceToConfirmationMapping, Result
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import JSONParser
from .serializers import ResourceSerializer, ResourceToConfirmationMappingSerializer, ResultSerializer
from datetime import datetime
from .services import SignatureService

# TODO: publish RSA verification key
host = "http://127.0.0.1:8000"
valid_decisions = ["ACCEPT", "REJECT"]
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
    def get(self, request, process_id):
        if process_id:
            if type(process_id) is int:
                resources = Resource.objects.filter(process_id=process_id)
                serializer = ResourceSerializer(resources, many=True)
                return Response(serializer.data, status=200)
            else:
                return Response(self, status=422)
        else:
            return Response(self, status=500)

    def post(self, request, process_id):
        data = request.data
        if request.query_params["decision"] not in valid_decisions:
            print(f"Invalid decision")
            return Response("Invalid decision", status=422)
        else:
            decision = request.query_params["decision"]

        if type(process_id) is not int:
            return Response(status=422)
        if type(data) is not QueryDict:
            return Response(status=415)
        if len(data.keys()) != 1:
            return Response(status=422)
        if "signed_CHEQ" not in data.keys():
            return Response(status=422)

        CHEQ = data["signed_CHEQ"]
        try:
            verifed_cheq = SignatureService.verify(self,CHEQ)
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
        return Response("Process id mismatch",status=400)


class ResourceCHEQView(APIView):
    def get(self, request, process_id):
        #TODO: this needs to be gated behind confirmation server auth
        if process_id:
            if type(process_id) is int:
                resources = Resource.objects.filter(process_id=process_id)
                if (resources.values()):

                    serializer = ResourceSerializer(resources, many=True)
                    resource_execution_uri = host + reverse("resource_server:resource", kwargs={"process_id":process_id}) + "execute"

                    CHEQ = {
                        "version": 1.0,
                        "operation": resource_execution_uri,
                        "operation name": process_id,
                        "inputs" :{
                            "parameters" : serializer.data
                        },
                        "date": datetime.now().strftime("%Y-%m-%d, %H:%M:%S.%f") #"2025-10-15T11:21:11.209504"
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
        else:
            return Response(status=422)

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
        # This is where the AI agent will ask for an action to be performed
        # Respond with 202, resource URI, and confirmation URI
        data = request.data
        if type(data) is not dict:
            return Response(status=415)
        if len(data.keys()) != 1:
            return Response(status=422)
        if "process_id" not in data.keys():
            return Response(status=422)
        if type(data["process_id"]) is not int:
            return Response(status=422)


        #return the resource uri and confirmation uri to the agent
        process_id = data["process_id"]

        # Add to the result model if not already there
        if not Result.objects.filter(process_id=process_id).exists():
            result = Result(process_id=process_id, confirmation_status="PENDING")
            result.save()

        resource_uri = host + reverse("resource_server:resource", kwargs=data)
        result_uri = host + reverse("resource_server:result", kwargs=data)

        confirmation_query = ResourceToConfirmationMapping.objects.filter(process_id=process_id)
        confirmation_serializer = ResourceToConfirmationMappingSerializer(confirmation_query, many=True)
        confirmation_uri = confirmation_serializer.data[0]["confirmation_uri"]

        response = {
            "resource_uri": resource_uri,
            "confirmation_uri": confirmation_uri,
            "result_uri": result_uri
        }
        return Response(response, status=202)

#TODO:
# implement API endpoint that provides public key for signature verification
# implement confirmation server auth
