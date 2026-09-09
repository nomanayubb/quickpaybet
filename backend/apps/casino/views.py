import json
import time
from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .providers import get_casino_provider
from .services import (
    InsufficientCasinoBalance,
    handle_round_settlement,
    handle_waija_balance_query,
    handle_waija_wallet_event,
)


class CasinoCallbackView(APIView):
    """
    Provider settle-notify endpoint - see apps.payments.views.PaymentWebhookView
    for the equivalent pattern on the payments side. No IP allowlist check
    here (the provider's own docs describe IP whitelisting only for
    outbound launch calls FROM this server TO the provider, not the
    reverse); the shared AES secret used to decrypt an encrypted callback
    body is what authenticates the request.

    throttle_classes = [] - a real gameplay session naturally makes many
    rapid calls here (a balance query plus a debit/credit per spin), so the
    project-wide anonymous rate limit (100/hour per IP, from
    DEFAULT_THROTTLE_CLASSES in settings.py) would throttle a normal
    player's OWN provider within minutes of active play - confirmed live,
    2026-09-09 (ngrok request log showed the provider's own callback
    getting a 429 mid-session). Security here comes from the shared secret
    verifying each request, not from public rate-limiting - the same
    reasoning already applies to WaijaWalletCallbackView below and
    apps.payments.views.PaymentWebhookView.
    """
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        provider = get_casino_provider()

        try:
            data = provider.decrypt_callback(request.body)
        except Exception as exc:  # noqa: BLE001 - malformed/undecryptable body
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        member_account = data.get('member_account')
        game_uid = data.get('game_uid')
        serial_number = data.get('serial_number')
        if not member_account or not game_uid or not serial_number:
            return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            handle_round_settlement(
                member_account=member_account,
                game_uid=game_uid,
                bet_amount=data.get('bet_amount', 0),
                win_amount=data.get('win_amount', 0),
                credit_amount=data.get('credit_amount'),
                serial_number=serial_number,
                raw_payload=data,
            )
        except Exception as exc:  # noqa: BLE001 - return validation errors as 400
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok'})


class WaijaWalletCallbackView(APIView):
    """
    Waija/SlotsGateway's own wallet callback contract - fundamentally
    different from CasinoCallbackView (SoftAPI's encrypted POST notify):
    every call is a signed GET carrying `action` of `balance` (no money
    moves), `debit`, or `credit` (rb=1 flags a rollback - the actual
    money direction is still whatever `action` says, per the provider's
    own docs). Always responds HTTP 200 with {error, balance} - the
    provider's protocol reserves HTTP error codes for transport failures
    only, never for wallet-level outcomes (see _timestamp_fresh/signature
    check below, which itself replies error:2 rather than 401/403).

    Reads fields from the GET query string first (standard GET semantics)
    and falls back to a JSON request body if a field is missing there,
    since the provider's own docs render the example payload as a JSON
    block without being fully explicit about which transport carries it.

    throttle_classes = [] - see CasinoCallbackView's docstring. This is the
    highest-volume endpoint in the whole integration (a balance query plus
    a debit/credit per spin) - a single active play session trips the
    project-wide 100/hour anonymous limit within minutes, silently
    breaking every bet/win after that for the rest of the hour. Caught
    live via ngrok's request log, 2026-09-09.
    """
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request):
        provider = get_casino_provider()
        data = self._extract(request)

        key = data.get('key', '')
        timestamp = data.get('timestamp', '')
        if not self._timestamp_fresh(timestamp) or not provider.verify_callback_signature(key, timestamp):
            return Response({'error': 2, 'balance': 0})

        username = data.get('username', '')
        action = data.get('action', '')

        try:
            if action == 'balance':
                balance = handle_waija_balance_query(username)
            elif action in ('debit', 'credit'):
                amount_cents = data.get('amount', 0)
                amount = (Decimal(str(amount_cents)) / Decimal('100')).quantize(Decimal('0.00000001'))
                balance = handle_waija_wallet_event(
                    username=username,
                    action=action,
                    amount=amount,
                    call_id=str(data.get('call_id', '')),
                    round_id=str(data.get('round_id', '')),
                    game_uid=str(data.get('game_id', '')),
                    is_rollback=str(data.get('rb', '0')) == '1',
                    event_type=str(data.get('type', '')),
                    raw_payload=data,
                )
            else:
                return Response({'error': 2, 'balance': 0})
        except InsufficientCasinoBalance as exc:
            return Response({'error': 1, 'balance': self._to_cents(exc.balance)})
        except Exception:  # noqa: BLE001 - unknown player, bad amount, etc. -> processing error
            return Response({'error': 2, 'balance': 0})

        return Response({'error': 0, 'balance': self._to_cents(balance)})

    @staticmethod
    def _to_cents(amount: Decimal) -> int:
        return int((amount * 100).to_integral_value())

    @staticmethod
    def _extract(request) -> dict:
        if request.GET:
            return request.GET.dict()
        try:
            return json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}

    @staticmethod
    def _timestamp_fresh(timestamp) -> bool:
        try:
            return abs(time.time() - int(timestamp)) <= 30
        except (TypeError, ValueError):
            return False
