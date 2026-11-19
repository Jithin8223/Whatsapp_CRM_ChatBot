import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return response
    # No tracebacks, SQL parameters, request bodies or cookies in user errors/logs.
    logger.error('API operation failed (%s).', type(exc).__name__)
    return Response({'detail': 'The operation could not be completed. Please retry or contact an administrator.'}, status=500)
