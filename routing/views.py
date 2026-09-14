from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET", "POST"])
def route(request):
    # Placeholder; replaced when API views are wired to the planner.
    return Response({"detail": "not implemented yet"}, status=501)
