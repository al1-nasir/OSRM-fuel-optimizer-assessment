"""
Custom exception handler for DRF.
Maps domain errors into clean JSON error responses.
"""

from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


class DomainError(Exception):
    """Base domain error with a machine-readable code."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def custom_exception_handler(exc, context):
    """DRF exception handler that formats domain errors."""
    if isinstance(exc, DomainError):
        return Response(
            {"error": {"code": exc.code, "message": exc.message}},
            status=exc.http_status,
        )

    # Fall back to DRF default handler
    response = exception_handler(exc, context)
    return response
