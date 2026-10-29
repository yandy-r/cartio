from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from rest_framework.routers import SimpleRouter

from cartio_controller.collectors import api, views

router = SimpleRouter(trailing_slash=False)
router.register("customers", api.CustomerViewSet, basename="customer")
router.register(
    r"customers/(?P<customer_pk>[0-9]+)/engagements", api.EngagementViewSet, basename="engagement"
)
router.register(r"customers/(?P<customer_pk>[0-9]+)/sites", api.SiteViewSet, basename="site")
router.register(
    r"customers/(?P<customer_pk>[0-9]+)/credentials", api.CredentialViewSet, basename="credential"
)
router.register(r"customers/(?P<customer_pk>[0-9]+)/jobs", api.JobViewSet, basename="job")
router.register(r"customers/(?P<customer_pk>[0-9]+)/runs", api.CollectionRunViewSet, basename="run")
router.register(
    r"customers/(?P<customer_pk>[0-9]+)/audit-events",
    api.AuditEventViewSet,
    basename="audit-event",
)

urlpatterns = [
    # The admin signs in through the engineer sign-in too, so it needs the authenticator code.
    path(
        "admin/login/",
        RedirectView.as_view(pattern_name="engineers:sign-in", query_string=True),
    ),
    path("admin/", admin.site.urls),
    path("api/v1/enroll", views.enroll, name="enroll"),
    path("api/v1/checkin", views.checkin, name="checkin"),
    path("api/v1/jobs/next", views.jobs_next, name="jobs-next"),
    path("api/v1/jobs/<str:job_id>/progress", views.job_progress, name="job-progress"),
    path("api/v1/jobs/<str:job_id>/chunks/<int:index>", views.job_chunk, name="job-chunk"),
    path("api/v1/jobs/<str:job_id>/result", views.job_result, name="job-result"),
    path("api/v1/", include(router.urls)),
    path("", include("cartio_controller.engineers.urls")),
]
