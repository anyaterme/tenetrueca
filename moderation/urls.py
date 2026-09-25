from django.urls import path

from moderation import views


app_name = 'moderation'

urlpatterns = [
    path('', views.moderation_queue, name='queue'),
    path('<int:pk>/', views.moderation_detail, name='detail'),
    path('<int:pk>/decision/', views.moderation_decide, name='decide'),
]
