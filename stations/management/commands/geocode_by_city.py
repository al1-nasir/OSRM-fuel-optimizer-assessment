"""
Management command: geocode_by_city

Bulk geocoding for stations where Census API failed.
Strategy: geocode unique city+state pairs via Nominatim, then
assign coordinates to ALL stations in that city.

This converts 3,500+ unique lookups to cover 6,000+ stations,
taking ~1 hour at Nominatim's 1 req/sec rate limit.

For stations sharing a city, each gets the city center coordinates.
While not exact street-level, this gives the route optimizer enough
accuracy to include stations in corridor searches (5-mile radius).

Usage:
    python manage.py geocode_by_city                      # geocode all
    python manage.py geocode_by_city --limit 100          # first 100 cities
    python manage.py geocode_by_city --dry-run            # preview
"""

import time

import httpx
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db.models import Count

from stations.models import FuelStation

US_LAT_RANGE = (17.0, 72.0)
US_LNG_RANGE = (-180.0, -65.0)

PHOTON_URL = "https://photon.komoot.io/api/"


class Command(BaseCommand):
    help = "Geocode failed stations by city+state center via Photon API (Komoot)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max number of city+state pairs to process (0 = all).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview without saving.",
        )

    def handle(self, *args, **options):
        # Find all unique city+state pairs that have failed stations
        pairs = (
            FuelStation.objects.filter(geocode_status="failed")
            .values("city", "state")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        if options["limit"] > 0:
            pairs = pairs[: options["limit"]]

        pairs = list(pairs)
        total_pairs = len(pairs)

        if total_pairs == 0:
            self.stdout.write(self.style.SUCCESS("No failed stations to geocode."))
            return

        total_stations = sum(p["count"] for p in pairs)
        self.stdout.write(
            f"Processing {total_pairs} city+state pairs "
            f"covering {total_stations} stations …\n"
        )

        dry_run = options["dry_run"]
        cities_resolved = 0
        cities_failed = 0
        stations_updated = 0

        client = httpx.Client(
            timeout=10.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )

        try:
            for i, pair in enumerate(pairs):
                city = pair["city"]
                state = pair["state"]
                count = pair["count"]

                query = f"{city}, {state}, USA"
                result = self._photon_search(client, query)

                if result:
                    cities_resolved += 1
                    if not dry_run:
                        updated = self._apply_to_city(city, state, result)
                        stations_updated += updated
                    else:
                        stations_updated += count

                    if i % 20 == 0 or i == total_pairs - 1:
                        self.stdout.write(
                            f"  [{i+1}/{total_pairs}] ✓ {city}, {state} "
                            f"→ ({result['lat']:.4f}, {result['lng']:.4f}) "
                            f"— {count} stations"
                        )
                else:
                    cities_failed += 1
                    if i % 20 == 0 or i == total_pairs - 1:
                        self.stdout.write(
                            f"  [{i+1}/{total_pairs}] ✗ {city}, {state} — no match"
                        )

                time.sleep(0.1)  # Photon handles fast request bursts easily

        finally:
            client.close()

        tag = " (DRY RUN)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\nGeocoding complete{tag}:\n"
                f"  Cities resolved: {cities_resolved}/{total_pairs}\n"
                f"  Cities failed:   {cities_failed}\n"
                f"  Stations updated:{stations_updated}\n"
            )
        )

        # Show final totals
        if not dry_run:
            total_geocoded = FuelStation.objects.filter(
                geocode_status="success"
            ).count()
            total_all = FuelStation.objects.count()
            self.stdout.write(
                f"Overall coverage: {total_geocoded}/{total_all} "
                f"({total_geocoded*100//total_all}%)"
            )

    def _photon_search(self, client: httpx.Client, query: str) -> dict | None:
        """Query Photon API. Returns {lat, lng, display_name} or None."""
        try:
            resp = client.get(
                PHOTON_URL,
                params={
                    "q": query,
                    "limit": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            features = data.get("features", [])
            if not features:
                return None

            hit = features[0]
            coords = hit.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                return None

            lng = float(coords[0])
            lat = float(coords[1])

            if not (
                US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1]
                and US_LNG_RANGE[0] <= lng <= US_LNG_RANGE[1]
            ):
                return None

            props = hit.get("properties", {})
            name_parts = [props.get("name"), props.get("city"), props.get("state")]
            disp_name = ", ".join([p for p in name_parts if p])

            return {
                "lat": lat,
                "lng": lng,
                "display_name": disp_name or query,
            }

        except (httpx.HTTPError, KeyError, ValueError) as e:
            return None

    @staticmethod
    def _apply_to_city(city: str, state: str, result: dict) -> int:
        """Apply geocode result to all failed stations in this city+state."""
        point = Point(result["lng"], result["lat"], srid=4326)
        updated = FuelStation.objects.filter(
            city=city,
            state=state,
            geocode_status="failed",
        ).update(
            location=point,
            geocode_status="success",
            geocode_error="",
            geocoded_address=f"[city_center] {result['display_name']}",
        )
        return updated
