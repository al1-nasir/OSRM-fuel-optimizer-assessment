"""
Management command: geocode_nominatim

Fallback geocoder for stations that the Census batch API couldn't resolve.
Uses the Nominatim (OpenStreetMap) API with a 3-tier strategy:

  1. Full address: "{address}, {city}, {state}, USA"
  2. City+state:   "{city}, {state}, USA"
  3. Mark as failed

Nominatim rate limit: 1 request per second (we obey this strictly).

Usage:
    python manage.py geocode_nominatim                    # geocode all failed
    python manage.py geocode_nominatim --limit 500        # first 500 only
    python manage.py geocode_nominatim --dry-run          # preview without saving
"""

import time

import httpx
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand

from stations.models import FuelStation

# Bounding box for continental US + Alaska + Hawaii (generous)
US_LAT_RANGE = (17.0, 72.0)
US_LNG_RANGE = (-180.0, -65.0)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


class Command(BaseCommand):
    help = "Geocode failed stations using Nominatim (OpenStreetMap) as fallback."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max number of stations to geocode (0 = all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be geocoded without saving.",
        )
        parser.add_argument(
            "--include-pending",
            action="store_true",
            help="Also geocode stations with 'pending' status.",
        )

    def handle(self, *args, **options):
        statuses = ["failed"]
        if options["include_pending"]:
            statuses.append("pending")

        qs = FuelStation.objects.filter(geocode_status__in=statuses).order_by(
            "opis_truckstop_id"
        )

        if options["limit"] > 0:
            qs = qs[: options["limit"]]

        stations = list(qs)
        total = len(stations)

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No stations to geocode."))
            return

        self.stdout.write(f"Geocoding {total} stations via Nominatim …")

        success_full = 0
        success_city = 0
        still_failed = 0
        dry_run = options["dry_run"]

        client = httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "FuelRoutePlanner/1.0 (student-project; contact@example.com)"
            },
        )

        try:
            for i, station in enumerate(stations):
                if i > 0 and i % 100 == 0:
                    self.stdout.write(f"  Progress: {i}/{total} …")

                # --- Tier 1: full address ---
                full_addr = f"{station.address}, {station.city}, {station.state}, USA"
                result = self._nominatim_search(client, full_addr)

                if result:
                    if not dry_run:
                        self._apply_result(station, result, "full_address")
                    success_full += 1
                    time.sleep(1.1)  # Nominatim rate limit
                    continue

                time.sleep(1.1)

                # --- Tier 2: city + state ---
                city_addr = f"{station.city}, {station.state}, USA"
                result = self._nominatim_search(client, city_addr)

                if result:
                    if not dry_run:
                        self._apply_result(station, result, "city_fallback")
                    success_city += 1
                    time.sleep(1.1)
                    continue

                time.sleep(1.1)

                # --- Tier 3: still failed ---
                if not dry_run:
                    station.geocode_status = "failed"
                    station.geocode_error = "Nominatim: no match for full address or city."
                    station.save(
                        update_fields=["geocode_status", "geocode_error", "updated_at"]
                    )
                still_failed += 1

        finally:
            client.close()

        tag = " (DRY RUN)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\nGeocoding complete{tag}:\n"
                f"  Full address match: {success_full}\n"
                f"  City fallback:      {success_city}\n"
                f"  Still failed:       {still_failed}\n"
                f"  Total processed:    {total}"
            )
        )

    def _nominatim_search(self, client: httpx.Client, query: str) -> dict | None:
        """
        Query Nominatim for a single address.
        Returns {"lat": float, "lng": float, "display_name": str} or None.
        """
        try:
            resp = client.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us",
                    "addressdetails": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return None

            hit = data[0]
            lat = float(hit["lat"])
            lng = float(hit["lon"])

            # Validate US bounds
            if not (US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1] and
                    US_LNG_RANGE[0] <= lng <= US_LNG_RANGE[1]):
                return None

            return {
                "lat": lat,
                "lng": lng,
                "display_name": hit.get("display_name", ""),
            }

        except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            self.stderr.write(f"  Nominatim error for '{query}': {e}")
            return None

    @staticmethod
    def _apply_result(station: FuelStation, result: dict, method: str):
        """Save the geocoded result to the station."""
        station.location = Point(result["lng"], result["lat"], srid=4326)
        station.geocode_status = "success"
        station.geocode_error = ""
        station.geocoded_address = f"[{method}] {result['display_name']}"
        station.save(
            update_fields=[
                "location",
                "geocode_status",
                "geocode_error",
                "geocoded_address",
                "updated_at",
            ]
        )
"""
