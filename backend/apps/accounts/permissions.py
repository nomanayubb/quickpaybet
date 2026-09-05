from rest_framework.permissions import BasePermission
from .models import User


class IsAdminOrMaster(BasePermission):
    message = 'Only admins and masters can perform this action.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in (User.Role.ADMIN, User.Role.MASTER)
        )
