from django.urls import path

from .views import ReportOverviewView, DailyReportView

app_name = 'reports'

urlpatterns = [
    path('reports/overview/', ReportOverviewView.as_view(), name='report-overview'),
    path('reports/daily/', DailyReportView.as_view(), name='report-daily'),
]
