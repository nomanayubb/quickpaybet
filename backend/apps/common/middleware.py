import zoneinfo

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
