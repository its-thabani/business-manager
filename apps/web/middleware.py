"""Login gate for the hosted app. Local DEBUG stays open so tests keep working."""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect


class RequireLoginMiddleware:
    """Send anonymous users to login when ``REQUIRE_LOGIN`` is on.

    ``/healthz`` stays public so Render can probe the service.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "REQUIRE_LOGIN", False):
            return self.get_response(request)
        path = request.path
        if (
            path.startswith("/admin/")
            or path.startswith("/accounts/")
            or path.startswith("/static/")
            or path in {"/healthz", "/healthz/"}
        ):
            return self.get_response(request)
        if not request.user.is_authenticated:
            login = settings.LOGIN_URL
            return redirect(f"{login}?next={path}")
        return self.get_response(request)
