"""Demo view that serves the Leaflet map page."""

from django.views.generic import TemplateView


class MapDemoView(TemplateView):
    template_name = "demo/map.html"
