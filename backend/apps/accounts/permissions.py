from rest_framework.permissions import BasePermission

from apps.common.permissions import IsAdminOrMaster
from .models import User

__all__ = ['IsAdminOrMaster', 'CanManageTargetUser', 'user_can_manage_target', 'roles_assignable_by']


def _is_full_admin(actor: User) -> bool:
    return bool(actor and (actor.is_superuser or actor.is_staff or actor.role == User.Role.ADMIN))


def user_can_manage_target(actor: User, target: User) -> bool:
    """
    Full admins (is_staff/is_superuser/role=admin) can manage any user.
    A master can only manage their own direct children (parent_id == master.id).
    Everyone else (agent/user) can manage nobody through this path.
    """
    if _is_full_admin(actor):
        return True
    if actor.role == User.Role.MASTER:
        return target.parent_id == actor.id
    return False


def roles_assignable_by(actor: User):
    """Which roles an actor is allowed to assign to a user they can manage."""
    if _is_full_admin(actor):
        return set(User.Role.values)
    if actor.role == User.Role.MASTER:
        return {User.Role.AGENT, User.Role.USER}
    return set()


class CanManageTargetUser(BasePermission):
    """
    Object-level permission for editing/viewing a specific user's admin fields.
    Must be combined with IsAdminOrMaster (or similar) for the view-level check.
    """

    message = 'You cannot manage this user.'

    def has_object_permission(self, request, view, obj):
        return user_can_manage_target(request.user, obj)
