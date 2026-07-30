"""
Management command: import_fuel_stations

Reads a CSV file of truck-stop fuel prices and creates or updates
FuelStation records in the database.

Usage:
    python manage.py import_fuel_stations path/to/fuel_prices.csv

CSV columns expected (exact header names):
    OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price

When duplicate OPIS Truckstop IDs appear (different rack IDs / prices),
the row with the **lowest** retail price is kept — this gives the best
price to the trip optimizer.
"""

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from stations.models import FuelStation

# CSV header → model field mapping
HEADER_MAP = {
    "OPIS Truckstop ID": "opis_truckstop_id",
    "Truckstop Name": "name",
    "Address": "address",
    "City": "city",
    "State": "state",
    "Rack ID": "rack_id",
    "Retail Price": "retail_price",
}

# Valid US state / territory codes (2-letter)
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
}


class Command(BaseCommand):
    help = "Import fuel-station data from a CSV file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            type=str,
            help="Path to the fuel-prices CSV file.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        self.stdout.write(f"Reading {csv_path} …")

        # ----- Phase 1: read & validate CSV, deduplicate by lowest price ----
        best_rows: dict[int, dict] = {}
        skipped = 0
        invalid_rows: list[tuple[int, str]] = []

        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)

            # Validate headers
            missing = set(HEADER_MAP.keys()) - set(reader.fieldnames or [])
            if missing:
                raise CommandError(
                    f"CSV is missing required columns: {', '.join(sorted(missing))}"
                )

            for line_no, raw_row in enumerate(reader, start=2):
                parsed = self._parse_row(raw_row, line_no, invalid_rows)
                if parsed is None:
                    skipped += 1
                    continue

                tsid = parsed["opis_truckstop_id"]
                if tsid not in best_rows or parsed["retail_price"] < best_rows[tsid]["retail_price"]:
                    best_rows[tsid] = parsed

        # ----- Phase 2: upsert into database -----------------------------------
        created = 0
        updated = 0

        for tsid, data in best_rows.items():
            obj, was_created = FuelStation.objects.update_or_create(
                opis_truckstop_id=tsid,
                defaults={
                    "name": data["name"],
                    "address": data["address"],
                    "city": data["city"],
                    "state": data["state"],
                    "rack_id": data["rack_id"],
                    "retail_price": data["retail_price"],
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        # ----- Phase 3: summary ------------------------------------------------
        self.stdout.write(self.style.SUCCESS(
            f"\nImport complete:\n"
            f"  Created:  {created}\n"
            f"  Updated:  {updated}\n"
            f"  Skipped:  {skipped}\n"
            f"  Invalid:  {len(invalid_rows)}\n"
            f"  Total unique stations: {len(best_rows)}"
        ))

        if invalid_rows:
            self.stdout.write(self.style.WARNING("\nInvalid rows:"))
            for line_no, reason in invalid_rows[:20]:
                self.stdout.write(f"  Line {line_no}: {reason}")
            if len(invalid_rows) > 20:
                self.stdout.write(f"  … and {len(invalid_rows) - 20} more.")

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        """Collapse multiple spaces, strip leading/trailing whitespace."""
        return re.sub(r"\s+", " ", value).strip()

    def _parse_row(
        self,
        raw: dict,
        line_no: int,
        invalid_rows: list,
    ) -> dict | None:
        """
        Validate and normalise a single CSV row.
        Returns parsed dict or None if the row should be skipped.
        """
        # --- OPIS Truckstop ID ---
        raw_id = (raw.get("OPIS Truckstop ID") or "").strip()
        try:
            tsid = int(raw_id)
        except (ValueError, TypeError):
            invalid_rows.append((line_no, f"Invalid OPIS Truckstop ID: '{raw_id}'"))
            return None

        # --- State ---
        state = self._normalize_whitespace(raw.get("State", "")).upper()
        if state not in US_STATE_CODES:
            # Skip non-US records (e.g. Canadian AB)
            invalid_rows.append((line_no, f"Non-US state '{state}' for ID {tsid}"))
            return None

        # --- Retail Price ---
        raw_price = (raw.get("Retail Price") or "").strip()
        try:
            price = Decimal(raw_price)
            if price <= 0:
                raise ValueError("non-positive")
        except (InvalidOperation, ValueError, TypeError):
            invalid_rows.append((line_no, f"Invalid Retail Price: '{raw_price}' for ID {tsid}"))
            return None

        # --- Rack ID ---
        raw_rack = (raw.get("Rack ID") or "").strip()
        rack_id = None
        if raw_rack:
            try:
                rack_id = int(raw_rack)
            except ValueError:
                pass  # non-critical — keep null

        # --- Text fields ---
        name = self._normalize_whitespace(raw.get("Truckstop Name", ""))
        address = self._normalize_whitespace(raw.get("Address", ""))
        city = self._normalize_whitespace(raw.get("City", ""))

        if not name or not address or not city:
            invalid_rows.append((line_no, f"Missing name/address/city for ID {tsid}"))
            return None

        return {
            "opis_truckstop_id": tsid,
            "name": name,
            "address": address,
            "city": city,
            "state": state,
            "rack_id": rack_id,
            "retail_price": price,
        }
