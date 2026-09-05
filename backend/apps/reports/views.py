from datetime import datetime

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsAdminOrMaster
from .services import get_overview_report, get_daily_report


class ReportOverviewView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrMaster]

    def get(self, request):
        return Response(get_overview_report())


class DailyReportView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrMaster]

    def get(self, request):
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        except ValueError:
            return Response(
                {'error': 'Dates must use YYYY-MM-DD format.'},
                status=400
            )

        return Response(get_daily_report(start_date, end_date))
