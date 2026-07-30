"""
Unit tests for the CSV import command.
"""

import csv
import os
import tempfile
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from stations.models import FuelStation

pytestmark = pytest.mark.django_db


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file for testing."""
    csv_path = tmp_path / "test_fuel.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "OPIS Truckstop ID", "Truckstop Name", "Address",
            "City", "State", "Rack ID", "Retail Price",
        ])
        # Normal rows
        writer.writerow([100, "Test Station A", "123 Main St", "Dallas", "TX", 500, "3.199"])
        writer.writerow([200, "Test Station B", "456 Oak Ave", "Atlanta", "GA", 600, "3.459"])
        # Duplicate ID with different price (should keep lowest)
        writer.writerow([100, "Test Station A - Rack 2", "123 Main St", "Dallas", "TX", 501, "2.999"])
        # Non-US (Canadian)
        writer.writerow([629, "FLYING J #850", "TCH-16", "Edmonton", "AB", 80, "4.399"])
    return csv_path


@pytest.fixture
def invalid_csv(tmp_path):
    """CSV with invalid rows."""
    csv_path = tmp_path / "bad_fuel.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "OPIS Truckstop ID", "Truckstop Name", "Address",
            "City", "State", "Rack ID", "Retail Price",
        ])
        # Invalid ID
        writer.writerow(["abc", "Bad Station", "123 St", "City", "TX", 1, "3.00"])
        # Invalid price
        writer.writerow([300, "Bad Price Station", "456 St", "City", "TX", 1, "not_a_price"])
        # Missing name
        writer.writerow([400, "", "789 St", "City", "TX", 1, "3.00"])
        # Valid row
        writer.writerow([500, "Good Station", "101 Rd", "Houston", "TX", 1, "3.50"])
    return csv_path


class TestImportFuelStations:
    def test_import_creates_stations(self, sample_csv):
        """Test that valid rows are imported correctly."""
        out = StringIO()
        call_command("import_fuel_stations", str(sample_csv), stdout=out)
        output = out.getvalue()

        # Should create 2 unique stations (100 and 200), skip Canadian
        assert FuelStation.objects.count() == 2
        assert "Created:  2" in output

    def test_duplicate_id_keeps_lowest_price(self, sample_csv):
        """When multiple rows have same OPIS ID, keep lowest price."""
        call_command("import_fuel_stations", str(sample_csv))

        station = FuelStation.objects.get(opis_truckstop_id=100)
        assert station.retail_price == Decimal("2.999")

    def test_non_us_states_skipped(self, sample_csv):
        """Canadian and other non-US records are skipped."""
        out = StringIO()
        call_command("import_fuel_stations", str(sample_csv), stdout=out)

        assert not FuelStation.objects.filter(state="AB").exists()

    def test_invalid_rows_reported(self, invalid_csv):
        """Invalid rows are reported but don't crash the import."""
        out = StringIO()
        call_command("import_fuel_stations", str(invalid_csv), stdout=out)
        output = out.getvalue()

        # Only the valid row should be imported
        assert FuelStation.objects.count() == 1
        assert FuelStation.objects.filter(opis_truckstop_id=500).exists()
        assert "Invalid" in output

    def test_update_on_reimport(self, sample_csv):
        """Re-importing the same CSV updates existing records."""
        call_command("import_fuel_stations", str(sample_csv))
        assert FuelStation.objects.count() == 2

        call_command("import_fuel_stations", str(sample_csv))
        # Count should remain the same (updated, not duplicated)
        assert FuelStation.objects.count() == 2

    def test_missing_columns_raises_error(self, tmp_path):
        """CSV without required columns raises CommandError."""
        csv_path = tmp_path / "missing_cols.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Name"])  # Wrong headers
            writer.writerow([1, "Test"])

        with pytest.raises(CommandError, match="missing required columns"):
            call_command("import_fuel_stations", str(csv_path))

    def test_file_not_found_raises_error(self):
        """Non-existent file raises CommandError."""
        with pytest.raises(CommandError, match="not found"):
            call_command("import_fuel_stations", "/nonexistent/file.csv")

    def test_decimal_price_precision(self, sample_csv):
        """Retail prices are stored with correct decimal precision."""
        call_command("import_fuel_stations", str(sample_csv))

        station = FuelStation.objects.get(opis_truckstop_id=200)
        assert station.retail_price == Decimal("3.459")

    def test_whitespace_normalization(self, tmp_path):
        """Extra whitespace in city/state/name is normalized."""
        csv_path = tmp_path / "whitespace.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "OPIS Truckstop ID", "Truckstop Name", "Address",
                "City", "State", "Rack ID", "Retail Price",
            ])
            writer.writerow([
                999, "  EXTRA   SPACES  ", "  123 Main St  ",
                "  Dallas   ", "  tx  ", 1, "3.00",
            ])

        call_command("import_fuel_stations", str(csv_path))

        station = FuelStation.objects.get(opis_truckstop_id=999)
        assert station.name == "EXTRA SPACES"
        assert station.city == "Dallas"
        assert station.state == "TX"
