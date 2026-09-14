from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from routing.serializers import RouteRequestSerializer
from routing.services.route_service import RouteServiceError, compute_route


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET", "POST"])
def route(request):
    """
    Plan a USA driving route and cost-effective fuel stops.

    POST JSON body: {"start": "Los Angeles, CA", "end": "Chicago, IL"}
    GET query params: ?start=Los%20Angeles,%20CA&end=Chicago,%20IL
    """
    if request.method == "GET":
        payload = {
            "start": request.query_params.get("start", ""),
            "end": request.query_params.get("end", ""),
        }
    else:
        payload = request.data

    serializer = RouteRequestSerializer(data=payload)
    serializer.is_valid(raise_exception=True)

    try:
        result = compute_route(
            serializer.validated_data["start"],
            serializer.validated_data["end"],
        )
    except RouteServiceError as exc:
        return Response({"detail": str(exc)}, status=exc.status)

    return Response(result, status=status.HTTP_200_OK)
