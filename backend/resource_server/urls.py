from django.urls import path
from . import views

app_name = "resource_server"
urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("resource/<str:process_token>/", views.ResourceView.as_view(), name="resource"),
    path("resource/<str:process_token>/select_flight/", views.SelectFlightView.as_view(), name="select_flight"),
    path("resource/<str:process_token>/cheq/", views.ResourceCHEQView.as_view(), name="resource_cheq"),
    path("public_key/", views.PublicKeyView.as_view(), name="public_key"),
    path("result/<int:process_id>/", views.ResultView.as_view(), name="result"),
    path("execute_process_with_confirmation/", views.ProcessExecutionWithConfirmation.as_view(), name="execute_process_with_confirmation"),
    # path("create_process/", views.CreateProcessView.as_view(), name="create_process")
]