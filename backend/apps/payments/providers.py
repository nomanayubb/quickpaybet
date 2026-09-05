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

    def _post(self, path: str, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError(
                'NOWPAYMENTS_API_KEY is not configured. '
                'Use PAYMENT_PROVIDER=mock for development.'
            )

        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode('utf-8'),
            headers=self._headers(),
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

    def create_withdrawal(self, user, amount: Decimal, address: str, currency: str = 'USDT', external_id: str = '') -> dict:
        """
        Skeleton for NOWPayments payouts.
        You must implement the actual /payment request with a live API key.
        """
        if not external_id:
            external_id = f"wd_{uuid.uuid4().hex}"

        payload = {
            'withdrawal': address,
            'currency': currency.upper(),
            'amount': str(amount),
        }

        # Placeholder result – real implementation would require an additional
        # endpoint and credentials. For now we mark it as always successful.
        return {
            'external_id': external_id,
            'provider': self.name,
            'status': 'pending',
        }

    def verify_webhook(self, raw_body: bytes, headers: dict | None = None) -> bool:
        """
        Validates the signature as an HMAC‐SHA512 of the raw body.
        The signature header MUST be 'x-nowpayments-sig'.
        If no IPN secret is configured, we accept the webhook for development.
        """
        if not self.ipn_secret:
            return True

        header_name = 'x-nowpayments-sig'
        signature = (headers or {}).get(header_name, '')

        if not signature:
            return False

        if isinstance(raw_body, str):
            raw_body = raw_body.encode('utf-8')

        expected = hmac.new(
            self.ipn_secret.encode('utf-8'),
            raw_body,
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
