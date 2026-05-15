from django.urls import path
from .views import ChatView, ClearMemoryView

app_name = "ai_agent"
urlpatterns = [
    path("chat/", ChatView.as_view(), name="chat"),
    path("clear_memory/", ClearMemoryView.as_view(), name="clear_memory"),
]