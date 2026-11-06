from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.urls import include, path, re_path


def frontend(request, path: str = ""):
    dist = settings.FRONTEND_DIST.resolve()
    if path:
        candidate = (dist / path).resolve()
        if dist == candidate or dist in candidate.parents:
            if candidate.is_file():
                return FileResponse(candidate.open("rb"))
        if path.startswith("assets/"):
            raise Http404("Missing asset")
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index.open("rb"))
    return HttpResponse(
        "Frontend is not built. From frontend/, run npm install && npm run build.\n",
        status=503,
        content_type="text/plain",
    )


urlpatterns = [
    path("api/", include("routing.urls")),
    path("", frontend),
    re_path(r"^(?P<path>.+)$", frontend),
]
