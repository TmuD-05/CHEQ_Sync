from django.urls import path

from . import views
from .views import TriggerView, PerformView
from .views_jwks import ConfirmationServerJWKSView

app_name = "confirmation_server"
urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path(".well-known/jwks.json", ConfirmationServerJWKSView.as_view(), name="jwks"),
    path("trigger_confirmation/", TriggerView.as_view(), name='triggers'),
    path("perform_confirmation", PerformView.as_view(), name='perform')
]