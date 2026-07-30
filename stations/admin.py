from django.contrib.gis import admin as gis_admin
from stations.models import FuelStation


@gis_admin.register(FuelStation)
class FuelStationAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "opis_truckstop_id",
        "name",
        "city",
        "state",
        "retail_price",
        "geocode_status",
        "updated_at",
    ]
    list_filter = ["state", "geocode_status"]
    search_fields = ["name", "city", "opis_truckstop_id"]
    readonly_fields = ["updated_at"]
