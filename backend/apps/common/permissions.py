from rest_framework.permissions import BasePermission


class IsAdminOrMaster(BasePermission):
    """Shared permission: only admins and masters may perform this action.

    This is the single canonical copy. Other apps import it from here instead
    of redefining it (it used to be duplicated verbatim in accounts, sports,
    reports, and audit).
    """

    message = 'Only admins and masters can perform this action.'

    def has_permission(self, request, view):
        from apps.accounts.models import User

        return (
            request.user
            and request.user.is_authenticated
            and (
                request.user.is_staff
                or request.user.role in (User.Role.ADMIN, User.Role.MASTER)
            )
        )
