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
    Skeleton for NOWPayments integration.

    Add your real API endpoint implementation when you have obtained
    an API key and production URL. The current version demonstrates the
    expected method signatures and error handling.
    """
    name = 'nowpayments'
    base_url = os.getenv('NOWPAYMENTS_API_URL', 'https://api.nowpayments.io/v1')
    api_key = os.getenv('NOWPAYMENTS_API_KEY', '')

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
            'price_currency': 'usd',          # adjust as needed
            'pay_currency': currency.lower(),
            'order_id': external_id,
            'ipn_callback_url': 'http://YOUR_DOMAIN/api/payments/webhook/',
        }

        result = self._post('/payment', payload)

        # In a real integration you would map NOWPayments' response fields.
        # The example below assumes your provider returns pay_address and payment_id.
        return {
            'external_id': str(result.get('payment_id', external_id)),
            'address': result.get('pay_address', ''),
            'provider': self.name,
            'payload': result,
        }

    def verify_webhook(self, data: dict, headers: dict | None = None) -> bool:
        # Add a real signature/hash validation here when you go live.
        return True


def get_payment_provider() -> BasePaymentProvider:
    provider_name = os.getenv('PAYMENT_PROVIDER', 'mock').lower()
    if provider_name == 'mock':
        return MockPaymentProvider()
    if provider_name == 'nowpayments':
        return NOWPaymentsProvider()
    raise NotImplementedError(f'Unknown payment provider: {provider_name}')
