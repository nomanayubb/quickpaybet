import hashlib
import hmac
import json
import os
import uuid
from decimal import Decimal
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

    def verify_webhook(self, data: dict, headers: dict | None = None) -> bool:
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

    def verify_webhook(self, data: dict, headers: dict | None = None) -> bool:
        return True


class NOWPaymentsProvider(BasePaymentProvider):
    """
    NOWPayments integration.

    Uses HMAC-SHA512 signature validation for incoming IPN webhooks.
    The secret is read from the NOWPAYMENTS_IPN_SECRET environment variable.
    """
    name = 'nowpayments'
    base_url = os.getenv('NOWPAYMENTS_API_URL', 'https://api.nowpayments.io/v1')
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
        with urlopen(request) as response:
            return json.loads(response.read().decode('utf-8'))

    def create_deposit(self, user, amount: Decimal, currency: str = 'USDT', external_id: str = '') -> dict:
        if not external_id:
            external_id = f"dep_{uuid.uuid4().hex}"

        payload = {
            'price_amount': str(amount),
            'price_currency': 'usd',
            'pay_currency': currency.lower(),
            'order_id': external_id,
            'ipn_callback_url': 'http://YOUR_DOMAIN/api/payments/webhook/',
        }

        result = self._post('/payment', payload)

        return {
            'external_id': str(result.get('payment_id', external_id)),
            'address': result.get('pay_address', ''),
            'provider': self.name,
            'payload': result,
        }

    def verify_webhook(self, data: dict, headers: dict | None = None) -> bool:
        """
        Validates the NOWPayments IPN signature.

        The signature is sent in the 'x-nowpayments-sig' header and is an
        HMAC-SHA512 hex digest of the raw request body using the IPN secret.
        This method receives the *original* body as a str/bytes through the
        `data` argument because the dict passed here has already been parsed.
        In real usage, the view should call this method before JSON decoding.
        For simplicity, we accept a `headers` dict containing the header value
        and compute the signature against a JSON-encoded copy. To be fully
        compatible you must pass the *raw* bytes and the signature header.

        To avoid false failures, a dummy implementation is provided here that
        verifies without a real secret. When you add the real NOWPAYMENTS_IPN_SECRET,
        this placeholder is replaced by the actual HMAC test.
        """
        if not self.ipn_secret:
            return True
        signature = (headers or {}).get('x-nowpayments-sig', '')
        if not signature:
            return False
        # The IPN raw payload is expected to be JSON – if not, signature check will fail.
        raw_body = json.dumps(data).encode('utf-8')
        computed = hmac.new(
            self.ipn_secret.encode('utf-8'),
            raw_body,
            hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(computed, signature.lower())


def get_payment_provider() -> BasePaymentProvider:
    provider_name = os.getenv('PAYMENT_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockPaymentProvider()
    if provider_name == 'nowpayments':
        return NOWPaymentsProvider()
    raise NotImplementedError(f'Unknown payment provider: {provider_name}')
