from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .providers import get_payment_provider
from .services import handle_deposit_success


class PaymentWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        external_id = request.data.get('external_id')
        provider_status = request.data.get('status', '').upper()

        if not external_id:
            return Response(
                {'error': 'external_id is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        provider = get_payment_provider()
        if provider.is_success_payment_status(provider_status):
            try:
                handle_deposit_success(external_id)
            except Exception as exc:  # noqa: BLE001 – catch and return validation error
                return Response(
                    {'error': str(exc)},
                    status=status.HTTP_400_BAD_REQUEST
                )
        elif provider_status in ('FAILED', 'CANCELLED'):
            from .models import CryptoPayment
            CryptoPayment.objects.filter(
                external_id=external_id,
                status=CryptoPayment.Status.PENDING
            ).update(status=CryptoPayment.Status.FAILED)

        return Response({'status': 'ok'})
