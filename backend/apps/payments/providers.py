import hashlib
import hmac
import json
import os
import uuid
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class BasePaymentProvider:
    name = 'base'

    def create_deposit(self, user, amount: Decimal, currency: str = 'USDT', external_id: str = '') -> dict:
        """
        Returns a dict with at least:
            - external_id: str
            - address: str
            - provider: str
            - payload: dict (optional provider-specific data)
        """
        raise NotImplementedError

    def create_withdrawal(self, user, amount: Decimal, address: str, currency: str = 'USDT', external_id: str = '') -> dict:
        raise NotImplementedError

    def verify_webhook(self, raw_body: bytes, headers: dict | None = None) -> bool:
        raise NotImplementedError

    def is_success_payment_status(self, status: str) -> bool:
        return status.upper() in ('FINISHED', 'COMPLETED', 'SUCCESS')


class MockPaymentProvider(BasePaymentProvider):
    name = 'mock'

    def create_deposit(self, user, amount: Decimal, currency: str = 'USDT', external_id: str = '') -> dict:
        if not external_id:
            external_id = f"dep_{uuid.uuid4().hex}"
        address = f"mockaddr_{external_id[-12:]}"
        return {
            'external_id': external_id,
            'address': address,
            'provider': self.name,
        }

    def create_withdrawal(self, user, amount: Decimal, address: str, currency: str = 'USDT', external_id: str = '') -> dict:
        if not external_id:
            external_id = f"wd_{uuid.uuid4().hex}"
        return {
            'external_id': external_id,
            'provider': self.name,
            'status': 'success',
        }

    def verify_webhook(self, raw_body: bytes, headers: dict | None = None) -> bool:
        return True


class NOWPaymentsProvider(BasePaymentProvider):
    """
    NOWPayments integration.

    Requires PAYMENT_PROVIDER=nowpayments and:
      NOWPAYMENTS_API_KEY
      NOWPAYMENTS_IPN_SECRET
      NOWPAYMENTS_API_URL (default: https://api.nowpayments.io/v1)
    """

    name = 'nowpayments'
    base_url = os.getenv('NOWPAYMENTS_API_URL', 'https://api.nowpayments.io/v1').rstrip('/')
    api_key = os.getenv('NOWPAYMENTS_API_KEY', '')
    ipn_secret = os.getenv('NOWPAYMENTS_IPN_SECRET', '')

    def _headers(self):
        return {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
        }

    def _post(self, path: str, payload: dict, headers: dict | None = None) -> dict:
        if not self.api_key:
            raise RuntimeError(
                'NOWPAYMENTS_API_KEY is not configured. '
                'Use PAYMENT_PROVIDER=mock for development.'
            )

        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers if headers is not None else self._headers(),
            method='POST',
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            body = exc.read().decode('utf-8')
            raise RuntimeError(
                f'NOWPayments HTTP {exc.code}: {body}'
            )

    def authenticate(self, email: str, password: str) -> str:
        """
        POST /v1/auth (no x-api-key header for this call) -> {"token": "<jwt>"}.
        The JWT is valid for ~5 minutes and is required (as a Bearer header,
        alongside x-api-key) to call /v1/payout and its /verify endpoint.

        The caller is responsible for never persisting `email`/`password` -
        they must only ever be used in-memory for this one call. This method
        does not store them either.
        """
        request = Request(
            self.base_url + '/auth',
            data=json.dumps({'email': email, 'password': password}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            body = exc.read().decode('utf-8')
            raise RuntimeError(f'NOWPayments auth failed (HTTP {exc.code}): {body}')

        token = result.get('token')
        if not token:
            raise RuntimeError(f'NOWPayments auth response missing token: {result}')
        return token

    def verify_payout(self, batch_id: str, verification_code: str, jwt_token: str) -> dict:
        """POST /v1/payout/<batch_id>/verify - confirms a payout batch with a 2FA code."""
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
            'Authorization': f'Bearer {jwt_token}',
        }
        return self._post(
            f'/payout/{batch_id}/verify',
            {'verification_code': verification_code},
            headers=headers,
        )

    def create_deposit(self, user, amount: Decimal, currency: str = 'USDT', external_id: str = '') -> dict:
        if not external_id:
            external_id = f"dep_{uuid.uuid4().hex}"

        normalized_currency = currency.upper()
        payload = {
            'price_amount': str(amount),
            'price_currency': 'usd',
            'pay_currency': normalized_currency,
            'order_id': external_id,
            'ipn_callback_url': os.getenv(
                'NOWPAYMENTS_IPN_CALLBACK_URL',
                'http://YOUR_DOMAIN/api/payments/webhook/',
            ),
        }

        result = self._post('/payment', payload)

        if not result.get('payment_id'):
            raise RuntimeError(
                'NOWPayments create-payment response missing payment_id: '
                + json.dumps(result)
            )

        return {
            'external_id': str(result['payment_id']),
            'address': result.get('pay_address', ''),
            'provider': self.name,
            'payload': result,
        }

    def create_withdrawal(self, user, amount: Decimal, address: str, currency: str = 'USDT', external_id: str = '', jwt_token: str | None = None) -> dict:
        """
        NOWPayments payouts (mass withdrawal / /v1/payout).

        NOWPayments' real payout endpoint requires BOTH the x-api-key header
        AND a JWT bearer token obtained via a separate email+password login
        (/v1/auth) - confirmed against NOWPayments' own official SDK source.
        The automatic withdrawal path (apps.payments.services.create_withdrawal_request)
        never has a JWT (no password is ever stored), so it correctly fails
        here and the wallet gets refunded - this is intentional, not a bug.

        The admin-initiated manual payout flow
        (apps.payments.services.initiate_manual_payout) obtains a short-lived
        JWT at the time an admin enters their NOWPayments password (never
        stored) and passes it in here as `jwt_token`.
        """
        if not external_id:
            external_id = f"wd_{uuid.uuid4().hex}"

        callback_url = os.getenv(
            'NOWPAYMENTS_IPN_CALLBACK_URL',
            'http://YOUR_DOMAIN/api/payments/webhook/',
        )
        payload = {
            'ipn_callback_url': callback_url,
            'withdrawals': [
                {
                    'address': address,
                    'currency': currency.lower(),
                    'amount': str(amount),
                    'ipn_callback_url': callback_url,
                }
            ],
        }

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
        }
        if jwt_token:
            headers['Authorization'] = f'Bearer {jwt_token}'

        result = self._post('/payout', payload, headers=headers)

        withdrawals = result.get('withdrawals') or []
        first = withdrawals[0] if withdrawals else {}

        return {
            'external_id': str(result.get('id', external_id)),
            'batch_withdrawal_id': result.get('id'),
            'provider': self.name,
            # Real payouts require 2FA verification before they actually
            # move funds; never report anything other than pending unless
            # the provider response explicitly says otherwise.
            'status': str(first.get('status', 'pending')).lower(),
            'payload': result,
        }

    def verify_webhook(self, raw_body: bytes, headers: dict | None = None) -> bool:
        """
        Validates the signature as an HMAC-SHA512 of the JSON body.

        IMPORTANT: NOWPayments does NOT sign the raw bytes as received. Per
        their IPN docs, the signature is computed over the JSON payload
        re-serialized with keys sorted recursively/alphabetically and no
        extra whitespace (`json.dumps(data, separators=(',', ':'), sort_keys=True)`),
        THEN hashed. Hashing the raw body directly (as this used to do) would
        essentially never match a real NOWPayments signature, since key order
        in the received bytes isn't guaranteed to already be sorted — this
        would have silently broken every real webhook verification.

        The signature header MUST be 'x-nowpayments-sig'.
        If no IPN secret is configured, we accept the webhook for development.
        """
        if not self.ipn_secret:
            return True

        header_name = 'x-nowpayments-sig'
        signature = (headers or {}).get(header_name, '')

        if not signature:
            return False

        if isinstance(raw_body, bytes):
            raw_body = raw_body.decode('utf-8')

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return False

        sorted_payload = json.dumps(payload, separators=(',', ':'), sort_keys=True)

        expected = hmac.new(
            self.ipn_secret.encode('utf-8'),
            sorted_payload.encode('utf-8'),
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(expected, signature.lower())


def get_payment_provider() -> BasePaymentProvider:
    provider_name = os.getenv('PAYMENT_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockPaymentProvider()
    if provider_name == 'nowpayments':
        return NOWPaymentsProvider()
    raise NotImplementedError(f'Unknown payment provider: {provider_name}')
