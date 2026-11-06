import json
import logging

from django.http import HttpResponseNotAllowed, JsonResponse

from .services.geocode import GeocodeError, search_places
from .services.http import UpstreamError
from .services.planner import PlanError, plan_trip
from .services.routing_api import RoutingError

logger = logging.getLogger(__name__)


def health(_request):
    return JsonResponse({"status": "ok"})


def geocode_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        results = search_places(request.GET.get("q", ""))
    except (GeocodeError, UpstreamError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"results": results})


def plan_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be JSON."}, status=400)
    try:
        return JsonResponse(plan_trip(body))
    except PlanError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    except (RoutingError, UpstreamError, GeocodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    except Exception:
        logger.exception("plan failed")
        return JsonResponse({"error": "Something went wrong while planning this trip."}, status=500)
