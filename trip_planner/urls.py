"""API URL configuration for the trip_planner app."""

from django.urls import path

from trip_planner.views import FuelPlanView

urlpatterns = [
    path("fuel-plan/", FuelPlanView.as_view(), name="fuel-plan"),
]
