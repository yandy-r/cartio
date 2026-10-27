from django.contrib import admin
from django.urls import path

from cartio_controller.collectors import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/enroll", views.enroll, name="enroll"),
    path("api/v1/checkin", views.checkin, name="checkin"),
]
