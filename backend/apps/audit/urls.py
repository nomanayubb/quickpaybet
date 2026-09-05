from django.urls import path

from .views import AuditLogListView

app_name = 'audit'

urlpatterns = [
    path('admin/audit-logs/', AuditLogListView.as_view(), name='audit-log-list'),
]
