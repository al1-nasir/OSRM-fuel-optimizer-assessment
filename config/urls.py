"""URL configuration for fuel_route_planner project."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/trips/", include("trip_planner.urls")),
    path("", include("trip_planner.demo_urls")),
]
