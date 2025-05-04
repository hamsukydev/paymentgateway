import uuid
import logging
import random
from datetime import datetime, timedelta

from .models import Transaction, Customer, Merchant

logger = logging.getLogger(__name__)

class SandboxService:
    """Service to handle sandbox test environment operations"""
    
    TEST_CARD_SCENARIOS = {
        "4242424242424242": "success",
        "4000000000000002": "declined",
        "4000000000000009": "insufficient_funds",
        "4000000000000003": "expired_card",
        "4000000000000127": "incorrect_cvc",
        "4000000000003220": "3d_secure_required",
        "4000000000003238": "3d_secure_failed",
        "4000000000003246": "3d_secure_timeout",
    }
    
    TEST_BANK_ACCOUNT_SCENARIOS = {
        "0000000000": "success",
        "0000000001": "insufficient_funds",
        "0000000002": "account_closed",
        "0000000003": "invalid_account"
    }

    @classmethod
    def process_test_transaction(cls, transaction_data, payment_details=None):
        """
        Process a test transaction in the sandbox environment
        
        Args:
            transaction_data (dict): Transaction data including amount, currency, etc.
            payment_details (dict): Payment method details (card, bank account)
            
        Returns:
            dict: Response with transaction status and details
        """
        transaction_data.setdefault('reference', f"SANDBOX-{uuid.uuid4().hex[:8].upper()}")
        
        # Default to success if no specific test scenario is triggered
        status = "success"
        decline_reason = None
        delay = 0
        
        # Determine outcome based on test card number
        if payment_details and 'card' in payment_details:
            card_number = payment_details['card'].get('number')
            if card_number in cls.TEST_CARD_SCENARIOS:
                scenario = cls.TEST_CARD_SCENARIOS[card_number]
                
                if scenario == "success":
                    status = "success"
                elif scenario == "3d_secure_required":
                    status = "requires_3d_secure"
                    delay = 2  # Simulate processing delay
                elif scenario == "3d_secure_timeout":
                    status = "failed"
                    decline_reason = "3D Secure authentication timed out"
                    delay = 5  # Longer delay for timeout
                else:
                    status = "failed"
                    decline_reason = scenario.replace('_', ' ').capitalize()
        
        # Determine outcome based on test bank account
        elif payment_details and 'bank_account' in payment_details:
            account_number = payment_details['bank_account'].get('account_number')
            if account_number in cls.TEST_BANK_ACCOUNT_SCENARIOS:
                scenario = cls.TEST_BANK_ACCOUNT_SCENARIOS[account_number]
                
                if scenario != "success":
                    status = "failed"
                    decline_reason = scenario.replace('_', ' ').capitalize()
        
        # For testing fraud detection
        if transaction_data.get('amount') == 123456.78:
            status = "failed"
            decline_reason = "Potential fraud detected"
        
        # Create transaction record for sandbox testing
        response = {
            "status": status,
            "reference": transaction_data['reference'],
            "message": "Sandbox test transaction processed" if status == "success" else f"Payment failed: {decline_reason}",
            "amount": transaction_data.get('amount'),
            "currency": transaction_data.get('currency'),
            "transaction_fee": cls._calculate_test_fee(transaction_data.get('amount', 0)),
            "transaction_date": datetime.now().isoformat(),
            "customer_email": transaction_data.get('email'),
            "payment_method": "card" if payment_details and 'card' in payment_details else "bank_transfer"
        }
        
        if status == "success":
            response["receipt_url"] = f"/sandbox/receipt/{transaction_data['reference']}/"
        
        if payment_details and 'card' in payment_details:
            card = payment_details['card']
            response["card_details"] = {
                "last4": card.get('number', '')[-4:] if card.get('number') else "4242",
                "card_type": cls._detect_card_type(card.get('number', '')),
                "exp_month": card.get('expiry_month', '12'),
                "exp_year": card.get('expiry_year', '2025')
            }
            
        return response

    @staticmethod
    def create_test_merchant():
        """Create a sandbox test merchant with test keys"""
        test_merchant = Merchant(
            name="Test Merchant",
            email="test@example.com",
            public_key=f"pk_test_{uuid.uuid4().hex[:16]}",
            secret_key=f"sk_test_{uuid.uuid4().hex[:16]}",
            is_active=True,
            is_test_account=True
        )
        test_merchant.save()
        return test_merchant

    @staticmethod
    def create_test_customer(merchant, email=None):
        """Create a test customer for sandbox testing"""
        if not email:
            email = f"test_{uuid.uuid4().hex[:8]}@example.com"
            
        customer = Customer(
            merchant=merchant,
            email=email,
            name=f"Test Customer {uuid.uuid4().hex[:4]}",
            phone="1234567890",
            customer_code=f"CUST-{uuid.uuid4().hex[:8].upper()}",
            is_test=True
        )
        customer.save()
        return customer
    
    @classmethod
    def simulate_webhooks(cls, event, data, merchant):
        """Simulate webhook delivery for test events"""
        from .webhook_notifier import WebhookNotifier
        
        # Only send webhooks if the merchant has a webhook URL configured
        if merchant.webhook_url:
            notifier = WebhookNotifier()
            
            # We're in test mode, so we'll just log instead of actually sending
            logger.info(f"SANDBOX: Would send webhook {event} to {merchant.webhook_url}")
            
            # Simulate webhook delivery result
            return {
                "status": "success" if random.random() > 0.1 else "failed",
                "event": event,
                "attempt_count": 1,
                "delivery_time": datetime.now().isoformat(),
                "response_code": 200 if random.random() > 0.1 else 500
            }
        
        return {"status": "skipped", "reason": "No webhook URL configured"}
    
    @staticmethod
    def _calculate_test_fee(amount):
        """Calculate transaction fee for test transactions"""
        return round(amount * 0.015 + 100, 2)  # 1.5% + ₦100 fee
        
    @staticmethod
    def _detect_card_type(card_number):
        """Detect card type from card number"""
        if not card_number:
            return "unknown"
            
        if card_number.startswith('4'):
            return "visa"
        elif card_number.startswith(('51', '52', '53', '54', '55')):
            return "mastercard"
        elif card_number.startswith(('34', '37')):
            return "amex"
        elif card_number.startswith('6'):
            return "discover"
        return "unknown"