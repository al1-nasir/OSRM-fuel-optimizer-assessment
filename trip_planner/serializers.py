"""
Request/response serializers for the fuel-plan API endpoint.
"""

from decimal import Decimal

from rest_framework import serializers


class FuelPlanRequestSerializer(serializers.Serializer):
    """Validates POST /api/v1/trips/fuel-plan/ input."""

    start = serializers.CharField(
        required=True,
        help_text="US start location (e.g. 'Dallas, TX').",
    )
    finish = serializers.CharField(
        required=True,
        help_text="US finish location (e.g. 'Atlanta, GA').",
    )
    starting_fuel_gallons = serializers.DecimalField(
        max_digits=4,
        decimal_places=2,
        required=False,
        default=Decimal("50"),
        min_value=Decimal("0"),
        max_value=Decimal("50"),
        help_text="Fuel in tank at departure (0–50 gallons). Default: 50.",
    )

    def validate_start(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Start location is required.")
        return value

    def validate_finish(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Finish location is required.")
        return value

    def validate(self, data):
        if data["start"].lower() == data["finish"].lower():
            raise serializers.ValidationError(
                "Start and finish must be different locations."
            )
        return data
