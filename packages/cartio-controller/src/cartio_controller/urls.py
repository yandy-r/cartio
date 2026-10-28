from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from cartio_controller.collectors import api, views

router = SimpleRouter(trailing_slash=False)
router.register("customers", api.CustomerViewSet, basename="customer")
router.register(
    r"customers/(?P<customer_pk>[0-9]+)/engagements", api.EngagementViewSet, basename="engagement"
)
router.register(r"customers/(?P<customer_pk>[0-9]+)/sites", api.SiteViewSet, basename="site")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/enroll", views.enroll, name="enroll"),
    path("api/v1/checkin", views.checkin, name="checkin"),
    path("api/v1/", include(router.urls)),
]
