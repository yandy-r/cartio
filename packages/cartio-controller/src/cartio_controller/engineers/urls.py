from django.urls import path

from . import views

app_name = "engineers"

urlpatterns = [
    path("", views.customers, name="customers"),
    path("customers/new", views.onboard, name="onboard"),
    # Per-customer screens, with or without the engagement. Without it, the current one shows.
    path("customers/<int:customer>", views.customer_detail, name="customer"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>",
        views.customer_detail,
        name="customer",
    ),
    path("customers/<int:customer>/site-map", views.sitemap, name="sitemap"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/site-map",
        views.sitemap,
        name="sitemap",
    ),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/site-map/export",
        views.sitemap_export,
        name="sitemap-export",
    ),
    path("customers/<int:customer>/inventory", views.inventory, name="inventory"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/inventory",
        views.inventory,
        name="inventory",
    ),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/inventory/export",
        views.inventory_export,
        name="inventory-export",
    ),
    path("customers/<int:customer>/findings", views.findings, name="findings"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/findings",
        views.findings,
        name="findings",
    ),
    path("customers/<int:customer>/compare", views.compare, name="compare"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/compare",
        views.compare,
        name="compare",
    ),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/compare/export",
        views.compare_export,
        name="compare-export",
    ),
    path("customers/<int:customer>/report", views.report, name="report"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/report",
        views.report,
        name="report",
    ),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/report/export",
        views.report_export,
        name="report-export",
    ),
    path("customers/<int:customer>/collectors", views.collectors, name="collectors"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/collectors",
        views.collectors,
        name="collectors",
    ),
    path(
        "customers/<int:customer>/collectors/<uuid:collector>/status",
        views.collector_status,
        name="collector-status",
    ),
    path("customers/<int:customer>/jobs", views.jobs, name="jobs"),
    path("customers/<int:customer>/engagements/<int:engagement>/jobs", views.jobs, name="jobs"),
    path("customers/<int:customer>/audit", views.audit_log, name="audit"),
    path(
        "customers/<int:customer>/engagements/<int:engagement>/audit",
        views.audit_log,
        name="audit",
    ),
    path("customers/<int:customer>/audit/export", views.audit_export, name="audit-export"),
    path("switcher", views.switcher, name="switcher"),
    path("search", views.search, name="search"),
    path("sign-in", views.sign_in, name="sign-in"),
    path("sign-in/code", views.verify, name="verify"),
    path("sign-in/authenticator", views.mfa_setup, name="mfa-setup"),
    path("sign-out", views.sign_out, name="sign-out"),
]
