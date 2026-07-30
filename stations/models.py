"""
FuelStation model — stores truck-stop fuel price data with PostGIS coordinates.

Coordinates are populated by a one-time batch geocoding process after CSV import.
Stations without valid coordinates are excluded from trip-planning recommendations.
"""

from django.contrib.gis.db import models as gis_models
from django.db import models


class FuelStation(models.Model):
    """
    A fuel station imported from the CSV price file.

    Each row is uniquely identified by ``opis_truckstop_id``.  When the CSV
    contains duplicate IDs with different prices (different rack IDs), the
    import process keeps the row with the **lowest** retail price.
    """

    opis_truckstop_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=7, decimal_places=3)

    # PostGIS point — populated by geocode_fuel_stations command
    location = gis_models.PointField(
        geography=True,
        srid=4326,
        null=True,
        blank=True,
        spatial_index=True,
    )

    # Geocoding workflow fields
    GEOCODE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("needs_review", "Needs Review"),
        ("skipped_non_us", "Skipped — Non-US"),
    ]
    geocode_status = models.CharField(
        max_length=30, default="pending", choices=GEOCODE_STATUS_CHOICES
    )
    geocode_error = models.TextField(blank=True, default="")
    geocoded_address = models.CharField(max_length=500, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["opis_truckstop_id"]
        indexes = [
            models.Index(fields=["state"], name="idx_station_state"),
            models.Index(fields=["geocode_status"], name="idx_geocode_status"),
        ]

    def __str__(self) -> str:
        return f"{self.name} (#{self.opis_truckstop_id}) — {self.city}, {self.state}"

    @property
    def geocodable_address(self) -> str:
        """Build the address string used for geocoding."""
        return f"{self.address}, {self.city}, {self.state}, USA"

    @property
    def is_geocoded(self) -> bool:
        return self.geocode_status == "success" and self.location is not None
