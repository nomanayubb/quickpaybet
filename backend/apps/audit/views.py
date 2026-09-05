from rest_framework import generics

from .models import AuditLog
from .serializers import AuditLogSerializer
from .permissions import IsAdminOrMaster


class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminOrMaster]
    queryset = AuditLog.objects.all()
