from django.db import models


class ConfirmationStatus(models.TextChoices):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
# todo have one consolidated table
class Resource(models.Model):
    process_id = models.IntegerField() #this is meant to denote a set of steps within a business process
    pub_date = models.DateTimeField("date published")
    # flight_data = models.JSONField()

    def __str__(self):
        return f"{self.process_id}, {self.sequence_number}"

    def get_all_steps_in_process(self, process_id):
        return self.process_id == process_id

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