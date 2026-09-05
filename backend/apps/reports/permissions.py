from rest_framework.permissions import BasePermission
from apps.accounts.models import User


class IsAdminOrMaster(BasePermission):
    message = 'Only admins and masters can view reports.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in (User.Role.ADMIN, User.Role.MASTER)
        )
