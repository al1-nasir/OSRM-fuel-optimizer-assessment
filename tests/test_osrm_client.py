"""
Unit tests for the OSRM client.
"""

from unittest.mock import patch, MagicMock

import httpx
import pytest

from trip_planner.services.osrm_client import OSRMError, get_route


class TestOSRMClient:
    @patch("trip_planner.services.osrm_client.cache")
    @patch("trip_planner.services.osrm_client.httpx.Client")
    def test_successful_route(self, mock_client_cls, mock_cache):
        """A successful OSRM response returns miles, minutes, and geometry."""
        mock_cache.get.return_value = None  # No cache hit

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": 1126540.0,  # ~700 miles in meters
                "duration": 37800.0,    # ~630 minutes in seconds
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-96.797, 32.777], [-84.388, 33.749]],
                },
            }],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = get_route(32.777, -96.797, 33.749, -84.388)

        assert result["distance_miles"] == pytest.approx(700.0, abs=5.0)
        assert result["estimated_duration_minutes"] > 0
        assert result["geometry"]["type"] == "LineString"

    @patch("trip_planner.services.osrm_client.cache")
    @patch("trip_planner.services.osrm_client.httpx.Client")
    def test_timeout_raises_osrm_error(self, mock_client_cls, mock_cache):
        """OSRM timeout raises OSRMError."""
        mock_cache.get.return_value = None

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client_cls.return_value = mock_client

        with pytest.raises(OSRMError, match="timed out"):
            get_route(32.777, -96.797, 33.749, -84.388)

    @patch("trip_planner.services.osrm_client.cache")
    def test_cache_hit_returns_cached(self, mock_cache):
        """Cached route is returned without external call."""
        cached_data = {
            "distance_miles": 700.0,
            "estimated_duration_minutes": 630.0,
            "geometry": {"type": "LineString", "coordinates": []},
        }
        mock_cache.get.return_value = cached_data

        result = get_route(32.777, -96.797, 33.749, -84.388)
        assert result == cached_data

    @patch("trip_planner.services.osrm_client.cache")
    @patch("trip_planner.services.osrm_client.httpx.Client")
    def test_bad_osrm_response(self, mock_client_cls, mock_cache):
        """Non-Ok OSRM response raises OSRMError."""
        mock_cache.get.return_value = None

        mock_response = MagicMock()
        mock_response.json.return_value = {"code": "NoRoute", "routes": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with pytest.raises(OSRMError):
            get_route(32.777, -96.797, 33.749, -84.388)
