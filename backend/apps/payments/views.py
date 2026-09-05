from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .providers import get_payment_provider
from .services import handle_deposit_success


class PaymentWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        provider = get_payment_provider()

        # Pass raw body to the provider so it can verify the HMAC signature.
        raw_body = request.body
        headers = request.headers

        valid = provider.verify_webhook(raw_body, headers=headers)
        if not valid:
            return Response(
                {'error': 'Invalid webhook signature.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse JSON only AFTER signature validation.
        import json
        try:
            data = json.loads(raw_body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                {'error': 'Invalid JSON body.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        external_id = data.get('external_id')
        provider_status = data.get('status', '').upper()

        if not external_id:
            return Response(
                {'error': 'external_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if provider.is_success_payment_status(provider_status):
            try:
                handle_deposit_success(external_id)
            except Exception as exc:  # noqa: BLE001 – catch and return validation error
                return Response(
                    {'error': str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif provider_status in ('FAILED', 'CANCELLED'):
            from .models import CryptoPayment
            CryptoPayment.objects.filter(
                external_id=external_id,
                status=CryptoPayment.Status.PENDING,
            ).update(status=CryptoPayment.Status.FAILED)

        return Response({'status': 'ok'})
