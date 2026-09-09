from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import create_audit_log
from .providers import get_payment_provider
from .services import handle_deposit_success


class PaymentWebhookView(APIView):
    """
    NOWPayments IPN endpoint - authenticated by its own HMAC signature
    (verify_webhook below), not by the caller's identity, so it's public
    (AllowAny) by design.

    throttle_classes = [] - the project-wide anonymous rate limit
    (100/hour per IP, DEFAULT_THROTTLE_CLASSES in settings.py) would apply
    here by default since this view has no explicit override, exactly the
    bug caught live on apps.casino's own webhook views (see their
    docstrings, 2026-09-09) - NOWPayments retries a failed/pending IPN
    delivery, and under real deposit/withdrawal volume this could start
    silently dropping legitimate webhook calls. Security here is the HMAC
    signature check, not public rate-limiting.
    """
    permission_classes = [AllowAny]
    throttle_classes = []

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
                payment = handle_deposit_success(external_id)
            except Exception as exc:  # noqa: BLE001 – catch and return validation error
                return Response(
                    {'error': str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            create_audit_log(
                user=payment.user,
                action='deposit_confirmed',
                target_type='crypto_payment',
                target_id=payment.id,
                metadata={'amount': str(payment.amount), 'provider': provider.name},
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        elif provider_status in ('FAILED', 'CANCELLED'):
            from .models import CryptoPayment
            CryptoPayment.objects.filter(
                external_id=external_id,
                status=CryptoPayment.Status.PENDING,
            ).update(status=CryptoPayment.Status.FAILED)

        return Response({'status': 'ok'})
