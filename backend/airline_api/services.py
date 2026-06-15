import os
import requests
from dotenv import load_dotenv
from resource_server.models import Flight
load_dotenv()


class AirlineService:
    def __init__(self):
        self.api_key = os.getenv('SERPAPI_API_KEY')
        self.engine = 'google_flights'

    def get_flights(self, params):
        origin = params['origin']
        destination = params['destination']
        outbound_date = params['outbound_date']

        # Query mock flights only
        flights = Flight.objects.filter(
            origin=origin,
            destination=destination,
            outbound_date=outbound_date
        ).values()

        return {
            "best_flights": list(flights),
            "other_flights": []
        }