from django.core.management.base import BaseCommand
from resource_server.models import Flight


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        Flight.objects.all().delete()

        flights_data = [
            # YVR to NRT
            {
                'origin': 'YVR',
                'destination': 'NRT',
                'outbound_date': '2026-06-25',
                'return_date': '2026-09-25',
                'airline': 'ANA',
                'flight_number': 'NH135',
                'departure_time': '12:30',
                'arrival_time': '14:40',
                'duration_minutes': 610,
                'stops': 0,
                'price': 1742.00,
                'airplane': 'Boeing 787'
            },
            {
                'origin': 'YVR',
                'destination': 'NRT',
                'outbound_date': '2026-06-25',
                'return_date': '2026-09-25',
                'airline': 'Air Canada',
                'flight_number': 'AC3',
                'departure_time': '13:10',
                'arrival_time': '14:55',
                'duration_minutes': 585,
                'stops': 0,
                'price': 1788.00,
                'airplane': 'Boeing 787'
            },
            {
                'origin': 'YVR',
                'destination': 'NRT',
                'outbound_date': '2026-06-25',
                'return_date': '2026-09-25',
                'airline': 'WestJet',
                'flight_number': 'WS110',
                'departure_time': '10:40',
                'arrival_time': '13:10',
                'duration_minutes': 820,
                'stops': 1,
                'price': 1583.00,
                'airplane': 'Boeing 737'
            },
            # HRE to YVR (one-way)
            {
                'origin': 'HRE',
                'destination': 'YVR',
                'outbound_date': '2026-06-25',
                'return_date': None,
                'airline': 'Air Canada',
                'flight_number': 'AC890',
                'departure_time': '08:00',
                'arrival_time': '16:30',
                'duration_minutes': 840,
                'stops': 1,
                'price': 950.00,
                'airplane': 'Boeing 777'
            },
        ]

        for data in flights_data:
            Flight.objects.create(**data)

        self.stdout.write("✓ Flights seeded")