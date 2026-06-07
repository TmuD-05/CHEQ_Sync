import os
import requests
from dotenv import load_dotenv

load_dotenv()


class AirlineService:
    def __init__(self):
        self.api_key = os.getenv('SERPAPI_API_KEY')
        self.engine = 'google_flights'

    def get_flights(self,params):
        parameters = {
            "api_key": self.api_key,
            "engine": self.engine,
            "departure_id": params['origin'],  # ← Map origin to departure_id
            "arrival_id": params['destination'],  # ← Map destination to arrival_id
            "outbound_date": params['outbound_date'],
            "type": params['type']
        }
        if 'return_date' in params:
            parameters['return_date'] = params['return_date']

        response =  requests.get("https://serpapi.com/search", params=parameters)
        return response.json()