import zoneinfo

from django.conf import settings
from django.utils import timezone

PAKISTAN_TZ = zoneinfo.ZoneInfo('Asia/Karachi')

# Paths under the custom admin section (kept as `panel/`/`dashboard/`, not
# `admin/` - see apps/web/urls.py for why). Anything here always displays in
# Pakistan time, regardless of who's viewing it or where from - this is an
# explicit client requirement, not a per-viewer preference.
ADMIN_PATH_PREFIXES = ('/panel/', '/dashboard/')

USER_TZ_COOKIE = 'user_tz'


class TimezoneMiddleware:
    """
    Admin pages always render in Pakistan time. Public/user-facing pages
    render in whatever timezone the visitor's own browser reports (detected
    client-side via a small script in base.html, which sets the `user_tz`
    cookie - see that template). Until that cookie exists (a brand-new
    visitor's very first request), public pages fall back to the site
    default (UTC, via settings.TIME_ZONE) rather than guessing.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(ADMIN_PATH_PREFIXES):
            timezone.activate(PAKISTAN_TZ)
        else:
            tzname = request.COOKIES.get(USER_TZ_COOKIE)
            if tzname:
                try:
                    timezone.activate(zoneinfo.ZoneInfo(tzname))
                except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                    timezone.deactivate()
            else:
                timezone.deactivate()

        response = self.get_response(request)
        timezone.deactivate()
        return response


class NoBrowserCacheMiddleware:
    """
    Every page here is session-dependent (shows whichever account is
    currently logged in). Without explicit no-cache headers, browsers can
    serve a stale snapshot from their "back/forward cache" when a user hits
    Back - e.g. showing a previously-logged-in user's page after switching
    to a different account, even though the real session has already
    changed. This forces the browser to always re-check with the server
    instead of showing a cached page from memory.

    Static files (served under STATIC_URL, e.g. /static/...) are
    deliberately excluded - those aren't session-dependent and should stay
    cacheable for performance.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(settings.STATIC_URL):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response['Pragma'] = 'no-cache'
        return response
