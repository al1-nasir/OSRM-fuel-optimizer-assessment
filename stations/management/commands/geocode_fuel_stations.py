"""
Management command: geocode_fuel_stations

Geocodes fuel stations that lack coordinates using the US Census Geocoder
batch endpoint.  The CSV has ~8,150 records — the Census batch API accepts
up to 10,000 at once, so one call should cover everything.

Usage:
    python manage.py geocode_fuel_stations              # geocode all pending
    python manage.py geocode_fuel_stations --only-missing  # only NULL locations
    python manage.py geocode_fuel_stations --retry-failed   # retry previously failed
"""

import csv
import io
import time

import httpx
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand

from stations.models import FuelStation

# Bounding box for continental US + Alaska + Hawaii (generous)
US_LAT_RANGE = (17.0, 72.0)
US_LNG_RANGE = (-180.0, -65.0)


class Command(BaseCommand):
    help = "Geocode fuel stations using the US Census Geocoder batch API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Only geocode stations with location IS NULL.",
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Also retry previously failed geocodes.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=9999,
            help="Max records per Census batch request (default: 9999).",
        )

    def handle(self, *args, **options):
        qs = FuelStation.objects.all()

        if options["only_missing"]:
            qs = qs.filter(location__isnull=True)
        elif options["retry_failed"]:
            qs = qs.filter(
                geocode_status__in=["pending", "failed", "needs_review"]
            )
        else:
            qs = qs.filter(geocode_status="pending")

        stations = list(qs.order_by("opis_truckstop_id"))
        total = len(stations)

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No stations to geocode."))
            return

        self.stdout.write(f"Geocoding {total} stations …")

        batch_size = options["batch_size"]
        success_count = 0
        fail_count = 0
        review_count = 0

        for batch_start in range(0, total, batch_size):
            batch = stations[batch_start : batch_start + batch_size]
            self.stdout.write(
                f"  Batch {batch_start // batch_size + 1}: "
                f"{len(batch)} stations …"
            )

            results = self._geocode_batch(batch)
            s, f, r = self._apply_results(batch, results)
            success_count += s
            fail_count += f
            review_count += r

            # Rate-limit between batches
            if batch_start + batch_size < total:
                time.sleep(2)

        self.stdout.write(self.style.SUCCESS(
            f"\nGeocoding complete:\n"
            f"  Success:      {success_count}\n"
            f"  Failed:       {fail_count}\n"
            f"  Needs review: {review_count}\n"
            f"  Total:        {total}"
        ))

    def _geocode_batch(self, stations: list[FuelStation]) -> dict[int, dict]:
        """
        Send a batch of stations to the Census Geocoder.
        Returns a dict mapping station PK → geocode result.
        """
        cfg = settings.FUEL_PLANNER

        # Build the CSV payload for the batch endpoint
        # Format: Unique ID, Street address, City, State, ZIP
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        for st in stations:
            writer.writerow([
                st.pk,
                st.address,
                st.city,
                st.state,
                "",  # ZIP — not available in the CSV
            ])

        csv_content = csv_buffer.getvalue()

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    cfg["CENSUS_BATCH_URL"],
                    data={
                        "benchmark": "Public_AR_Current",
                        "returntype": "locations",
                    },
                    files={
                        "addressFile": ("addresses.csv", csv_content, "text/csv"),
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            self.stderr.write(
                self.style.ERROR(f"Census batch API error: {e}")
            )
            return {}

        return self._parse_batch_response(response.text)

    def _parse_batch_response(self, response_text: str) -> dict[int, dict]:
        """
        Parse the Census Geocoder batch response CSV.
        Response columns:
            ID, Input Address, Match Status, Match Type,
            Matched Address, Coordinates (lng,lat), TIGER Line ID, Side
        """
        results = {}
        reader = csv.reader(io.StringIO(response_text))

        for row in reader:
            if len(row) < 6:
                continue

            try:
                station_pk = int(row[0].strip('"').strip())
            except (ValueError, IndexError):
                continue

            match_status = row[2].strip().strip('"').lower() if len(row) > 2 else ""
            match_type = row[3].strip().strip('"').lower() if len(row) > 3 else ""
            matched_address = row[4].strip().strip('"') if len(row) > 4 else ""
            coords_str = row[5].strip().strip('"') if len(row) > 5 else ""

            if match_status == "match" and coords_str:
                try:
                    lng_str, lat_str = coords_str.split(",")
                    lng = float(lng_str.strip())
                    lat = float(lat_str.strip())
                    results[station_pk] = {
                        "status": "success",
                        "lat": lat,
                        "lng": lng,
                        "matched_address": matched_address,
                        "match_type": match_type,
                    }
                except (ValueError, TypeError):
                    results[station_pk] = {
                        "status": "failed",
                        "error": f"Could not parse coordinates: {coords_str}",
                    }
            elif match_status == "no_match":
                results[station_pk] = {
                    "status": "failed",
                    "error": "No match from Census Geocoder.",
                }
            elif match_status == "tie":
                results[station_pk] = {
                    "status": "needs_review",
                    "error": "Ambiguous match (tie) from Census Geocoder.",
                }
            else:
                results[station_pk] = {
                    "status": "failed",
                    "error": f"Unexpected match status: '{match_status}'",
                }

        return results

    def _apply_results(
        self,
        stations: list[FuelStation],
        results: dict[int, dict],
    ) -> tuple[int, int, int]:
        """Apply geocode results to station records. Returns (success, fail, review) counts."""
        success = fail = review = 0

        for st in stations:
            result = results.get(st.pk)

            if result is None:
                # Station was not in the response — mark failed
                st.geocode_status = "failed"
                st.geocode_error = "No response from Census Geocoder."
                st.save(update_fields=["geocode_status", "geocode_error", "updated_at"])
                fail += 1
                continue

            if result["status"] == "success":
                lat = result["lat"]
                lng = result["lng"]

                # Validate coordinates are within US bounds
                if not (US_LAT_RANGE[0] <= lat <= US_LAT_RANGE[1] and
                        US_LNG_RANGE[0] <= lng <= US_LNG_RANGE[1]):
                    st.geocode_status = "needs_review"
                    st.geocode_error = (
                        f"Coordinates ({lat}, {lng}) outside US bounds."
                    )
                    st.save(update_fields=[
                        "geocode_status", "geocode_error", "updated_at"
                    ])
                    review += 1
                    continue

                # Point(longitude, latitude) — GeoJSON order
                st.location = Point(lng, lat, srid=4326)
                st.geocode_status = "success"
                st.geocode_error = ""
                st.geocoded_address = result.get("matched_address", "")
                st.save(update_fields=[
                    "location", "geocode_status", "geocode_error",
                    "geocoded_address", "updated_at",
                ])
                success += 1

            elif result["status"] == "needs_review":
                st.geocode_status = "needs_review"
                st.geocode_error = result.get("error", "")
                st.save(update_fields=[
                    "geocode_status", "geocode_error", "updated_at"
                ])
                review += 1

            else:
                st.geocode_status = "failed"
                st.geocode_error = result.get("error", "")
                st.save(update_fields=[
                    "geocode_status", "geocode_error", "updated_at"
                ])
                fail += 1

        return success, fail, review
