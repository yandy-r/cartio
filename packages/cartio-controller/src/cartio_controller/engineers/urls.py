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
    path("customers/<int:customer>/jobs", views.jobs, name="jobs"),
    path("customers/<int:customer>/engagements/<int:engagement>/jobs", views.jobs, name="jobs"),
    path("switcher", views.switcher, name="switcher"),
    path("sign-in", views.sign_in, name="sign-in"),
    path("sign-in/code", views.verify, name="verify"),
    path("sign-in/authenticator", views.mfa_setup, name="mfa-setup"),
    path("sign-out", views.sign_out, name="sign-out"),
]
