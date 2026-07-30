"""Demo URL for the Leaflet map page."""

from django.urls import path
from trip_planner.demo_views import MapDemoView

urlpatterns = [
    path("demo/map/", MapDemoView.as_view(), name="map-demo"),
]
