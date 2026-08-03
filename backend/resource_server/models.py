from django.db import models


class ConfirmationStatus(models.TextChoices):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
# todo have one consolidated table
class Resource(models.Model):
    process_id = models.IntegerField() #this is meant to denote a set of steps within a business process
    pub_date = models.DateTimeField("date published")
    selected_flight = models.JSONField(null=True, blank=True)


    def __str__(self):
        return f"{self.process_id}"

    @classmethod
    def get_all_steps_in_process(cls, process_id):
        return cls.objects.filter(process_id=process_id)

class ResourceToConfirmationMapping(models.Model):
    process_id = models.IntegerField() #this is the field that will join a process to a confirmation URI
    confirmation_uri = models.URLField() #this is the confirmation URI

    def __str__(self):
        return f"{self.process_id}, {self.confirmation_uri}"

    def get_confirmation_uri(self, process_id):
        return self.process_id == process_id

class Result(models.Model):
    process_id = models.IntegerField() #this is meant to denote a set of steps within a business process
    confirmation_status = models.CharField(
        max_length=8,
        choices=ConfirmationStatus.choices,
        default=ConfirmationStatus.PENDING
    ) #this is the status of confirmation

    def __str__(self):
        return f"{self.process_id}, {self.confirmation_status}"

    def get_all_confirmation_status(self, process_id):
        return self.process_id == process_id


class Process(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Process {self.id}"

class Flight(models.Model):
    process_id = models.IntegerField(null=True, blank=True)
    origin = models.CharField(max_length=3)
    destination = models.CharField(max_length=3)
    outbound_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)

    airline = models.CharField(max_length=50)
    flight_number = models.CharField(max_length=10)
    departure_time = models.TimeField()
    arrival_time = models.TimeField()
    duration_minutes = models.IntegerField()
    stops = models.IntegerField(default=0)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    airplane = models.CharField(max_length=50)

    class Meta:
        ordering = ['price']

    def __str__(self):
        return f"{self.airline} {self.flight_number}"


class ConsumedNonce(models.Model):
    nonce = models.CharField(max_length=64, primary_key=True)
    process_id = models.IntegerField()
    consumed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Nonce {self.nonce} for Process {self.process_id}"