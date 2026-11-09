from django.urls import path

from . import views

app_name = 'chatbot'
urlpatterns = [
    path('api/session/', views.session, name='session'),
    path('api/messages/', views.messages, name='messages'),
    path('api/reset/', views.reset, name='reset'),
]
