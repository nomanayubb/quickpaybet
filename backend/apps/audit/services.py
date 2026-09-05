import ipaddress

from django.utils import timezone

from .models import AuditLog


def create_audit_log(user, action, target_type='', target_id='', metadata=None, ip_address=None):
    """
    Store an audit record.
    Call this explicitly after successful mutations that require audit.
    """
    AuditLog.objects.create(
        user=user,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else '',
        metadata=metadata or {},
        ip_address=ip_address,
    )
