from django.urls import path

from . import views

app_name = "engineers"

urlpatterns = [
    path("", views.customers, name="customers"),
    path("customers/<int:pk>", views.customer_detail, name="customer"),
    path("customers/<int:pk>/jobs", views.jobs, name="jobs"),
    path("sign-in", views.sign_in, name="sign-in"),
    path("sign-in/code", views.verify, name="verify"),
    path("sign-in/authenticator", views.mfa_setup, name="mfa-setup"),
    path("sign-out", views.sign_out, name="sign-out"),
]
