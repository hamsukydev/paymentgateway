import logging
import hmac
import hashlib
import json
from django.conf import settings
from django.http import HttpResponse
from .models import Transaction, Merchant
from .fraud_detector import analyze_transaction

logger = logging.getLogger(__name__)

class WebhookHandler:
    """Base class for handling webhooks from payment providers"""
    
    def __init__(self, request, provider):
        self.request = request
        self.provider = provider
        self.payload = None
        self.signature = None
        self.event_type = None
        self.transaction_reference = None
    
    def process(self):
        """Process the webhook request"""
        try:
            self.extract_data()
            
            if not self.validate_signature():
                logger.warning(f"Invalid signature for {self.provider} webhook")
                return HttpResponse("Invalid signature", status=401)
            
            event_handler = self.get_event_handler()
            if (event_handler):
                return event_handler()
            else:
                logger.info(f"Unhandled {self.provider} event type: {self.event_type}")
                return HttpResponse("Event type not handled", status=200)
                
        except Exception as e:
            logger.error(f"Error processing {self.provider} webhook: {str(e)}")
            return HttpResponse("Error processing webhook", status=500)
    
    def extract_data(self):
        """Extract data from the webhook request"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def validate_signature(self):
        """Validate the webhook signature"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def get_event_handler(self):
        """Get the handler function for the event type"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def handle_successful_charge(self):
        """Handle a successful charge event"""
        raise NotImplementedError("Subclasses must implement this method")
    
    def handle_failed_charge(self):
        """Handle a failed charge event"""
        raise NotImplementedError("Subclasses must implement this method")


class PaystackWebhookHandler(WebhookHandler):
    """Handler for Paystack webhooks"""
    
    def __init__(self, request):
        super().__init__(request, "paystack")
    
    def extract_data(self):
        """Extract data from the webhook request"""
        try:
            self.payload = json.loads(self.request.body)
            self.signature = self.request.headers.get('X-Paystack-Signature')
            self.event_type = self.payload.get('event')
            
            if 'data' in self.payload and 'reference' in self.payload['data']:
                self.transaction_reference = self.payload['data']['reference']
        except Exception as e:
            logger.error(f"Failed to extract Paystack webhook data: {str(e)}")
            raise
    
    def validate_signature(self):
        """Validate the webhook signature"""
        if not self.signature:
            logger.warning("No Paystack signature found in webhook request")
            return False
        
        # Get the Paystack secret key from settings
        secret_key = settings.PAYSTACK_SECRET_KEY
        
        # Generate signature
        expected_signature = hmac.new(
            secret_key.encode('utf-8'),
            self.request.body,
            hashlib.sha512
        ).hexdigest()
        
        return hmac.compare_digest(self.signature, expected_signature)
    
    def get_event_handler(self):
        """Get the handler function for the event type"""
        handlers = {
            'charge.success': self.handle_successful_charge,
            'charge.failed': self.handle_failed_charge,
            'transfer.success': self.handle_successful_transfer,
            'transfer.failed': self.handle_failed_transfer,
            'subscription.create': self.handle_subscription_created,
            'subscription.disable': self.handle_subscription_disabled
        }
        
        return handlers.get(self.event_type)
    
    def handle_successful_charge(self):
        """Handle a successful charge event"""
        if not self.transaction_reference:
            logger.error("No transaction reference found in Paystack webhook")
            return HttpResponse("Missing transaction reference", status=400)
        
        try:
            transaction = Transaction.objects.get(reference=self.transaction_reference)
            
            # Update transaction status if it's not already marked as successful
            if transaction.status != 'success':
                transaction.status = 'success'
                
                # Get payment details from payload
                payment_data = self.payload.get('data', {})
                payment_method = payment_data.get('channel', 'card')
                
                # Set payment method
                transaction.payment_method = payment_method
                
                # Save card details in metadata for recurring billing (if payment method is card)
                if payment_method == 'card' and 'authorization' in payment_data:
                    auth = payment_data['authorization']
                    
                    # Update transaction metadata
                    metadata = transaction.get_metadata() or {}
                    metadata['card'] = {
                        'bin': auth.get('bin'),
                        'last4': auth.get('last4'),
                        'exp_month': auth.get('exp_month'),
                        'exp_year': auth.get('exp_year'),
                        'card_type': auth.get('card_type'),
                        'bank': auth.get('bank')
                    }
                    transaction.set_metadata(metadata)
                    
                    # Save payment method in customer profile if customer exists
                    if transaction.customer:
                        transaction.customer.save_payment_method({
                            'provider': 'paystack',
                            'authorization_code': auth.get('authorization_code'),
                            'card_type': auth.get('card_type'),
                            'last4': auth.get('last4'),
                            'exp_month': auth.get('exp_month'),
                            'exp_year': auth.get('exp_year'),
                            'bank': auth.get('bank')
                        })
                
                transaction.save()
                
                # Run fraud detection
                ip_address = payment_data.get('ip_address')
                device_fingerprint = payment_data.get('device_fingerprint')
                analyze_transaction(transaction, ip=ip_address, device_fingerprint=device_fingerprint)
                
                logger.info(f"Transaction {self.transaction_reference} marked as successful")
            
            return HttpResponse("Webhook processed", status=200)
            
        except Transaction.DoesNotExist:
            logger.warning(f"Transaction {self.transaction_reference} not found for Paystack webhook")
            return HttpResponse("Transaction not found", status=404)
        except Exception as e:
            logger.error(f"Error processing Paystack successful charge: {str(e)}")
            return HttpResponse("Error processing webhook", status=500)
    
    def handle_failed_charge(self):
        """Handle a failed charge event"""
        if not self.transaction_reference:
            return HttpResponse("Missing transaction reference", status=400)
        
        try:
            transaction = Transaction.objects.get(reference=self.transaction_reference)
            
            # Update transaction status if it's pending
            if transaction.status == 'pending':
                transaction.status = 'failed'
                transaction.save()
                logger.info(f"Transaction {self.transaction_reference} marked as failed")
            
            return HttpResponse("Webhook processed", status=200)
            
        except Transaction.DoesNotExist:
            logger.warning(f"Transaction {self.transaction_reference} not found for Paystack webhook")
            return HttpResponse("Transaction not found", status=404)
        except Exception as e:
            logger.error(f"Error processing Paystack failed charge: {str(e)}")
            return HttpResponse("Error processing webhook", status=500)
    
    def handle_successful_transfer(self):
        """Handle a successful transfer event"""
        # Implementation for handling merchant payouts
        return HttpResponse("Transfer webhook processed", status=200)
    
    def handle_failed_transfer(self):
        """Handle a failed transfer event"""
        # Implementation for handling failed merchant payouts
        return HttpResponse("Failed transfer webhook processed", status=200)
    
    def handle_subscription_created(self):
        """Handle a subscription created event"""
        # Implementation for handling new subscriptions
        return HttpResponse("Subscription created webhook processed", status=200)
    
    def handle_subscription_disabled(self):
        """Handle a subscription disabled event"""
        # Implementation for handling disabled subscriptions
        return HttpResponse("Subscription disabled webhook processed", status=200)


class FlutterwaveWebhookHandler(WebhookHandler):
    """Handler for Flutterwave webhooks"""
    
    def __init__(self, request):
        super().__init__(request, "flutterwave")
    
    def extract_data(self):
        """Extract data from the webhook request"""
        try:
            self.payload = json.loads(self.request.body)
            self.signature = self.request.headers.get('verif-hash')
            self.event_type = self.payload.get('event.type')
            
            # In Flutterwave, the reference is usually in 'data.tx_ref'
            data = self.payload.get('data', {})
            self.transaction_reference = data.get('tx_ref')
        except Exception as e:
            logger.error(f"Failed to extract Flutterwave webhook data: {str(e)}")
            raise
    
    def validate_signature(self):
        """Validate the webhook signature"""
        if not self.signature:
            logger.warning("No Flutterwave signature found in webhook request")
            return False
        
        # Get the Flutterwave secret hash from settings
        secret_hash = settings.FLUTTERWAVE_SECRET_HASH
        
        # For Flutterwave, just compare the hash directly
        return hmac.compare_digest(self.signature, secret_hash)
    
    def get_event_handler(self):
        """Get the handler function for the event type"""
        handlers = {
            'charge.completed': self.handle_successful_charge,
            'charge.failed': self.handle_failed_charge,
            'transfer.completed': self.handle_successful_transfer,
            'transfer.failed': self.handle_failed_transfer,
        }
        
        return handlers.get(self.event_type)
    
    def handle_successful_charge(self):
        """Handle a successful charge event"""
        if not self.transaction_reference:
            return HttpResponse("Missing transaction reference", status=400)
        
        try:
            transaction = Transaction.objects.get(reference=self.transaction_reference)
            
            # Update transaction status if it's not already marked as successful
            if transaction.status != 'success':
                transaction.status = 'success'
                
                # Get payment details from payload
                payment_data = self.payload.get('data', {})
                payment_method = payment_data.get('payment_type', 'card')
                
                # Set payment method
                transaction.payment_method = payment_method
                
                # Save card details in metadata for recurring billing (if payment method is card)
                if payment_method == 'card' and 'card' in payment_data:
                    card = payment_data['card']
                    
                    # Update transaction metadata
                    metadata = transaction.get_metadata() or {}
                    metadata['card'] = {
                        'bin': card.get('first_6digits'),
                        'last4': card.get('last_4digits'),
                        'exp_month': card.get('expiry_month'),
                        'exp_year': card.get('expiry_year'),
                        'card_type': card.get('type'),
                        'bank': card.get('issuer')
                    }
                    transaction.set_metadata(metadata)
                    
                    # Save payment method in customer profile if customer exists
                    if transaction.customer:
                        transaction.customer.save_payment_method({
                            'provider': 'flutterwave',
                            'authorization_code': card.get('token'),
                            'card_type': card.get('type'),
                            'last4': card.get('last_4digits'),
                            'exp_month': card.get('expiry_month'),
                            'exp_year': card.get('expiry_year'),
                            'bank': card.get('issuer')
                        })
                
                transaction.save()
                
                # Run fraud detection
                ip_address = payment_data.get('ip')
                device_fingerprint = payment_data.get('device_fingerprint')
                analyze_transaction(transaction, ip=ip_address, device_fingerprint=device_fingerprint)
                
                logger.info(f"Transaction {self.transaction_reference} marked as successful")
            
            return HttpResponse("Webhook processed", status=200)
            
        except Transaction.DoesNotExist:
            logger.warning(f"Transaction {self.transaction_reference} not found for Flutterwave webhook")
            return HttpResponse("Transaction not found", status=404)
        except Exception as e:
            logger.error(f"Error processing Flutterwave successful charge: {str(e)}")
            return HttpResponse("Error processing webhook", status=500)
    
    def handle_failed_charge(self):
        """Handle a failed charge event"""
        if not self.transaction_reference:
            return HttpResponse("Missing transaction reference", status=400)
        
        try:
            transaction = Transaction.objects.get(reference=self.transaction_reference)
            
            # Update transaction status if it's pending
            if transaction.status == 'pending':
                transaction.status = 'failed'
                transaction.save()
                logger.info(f"Transaction {self.transaction_reference} marked as failed")
            
            return HttpResponse("Webhook processed", status=200)
            
        except Transaction.DoesNotExist:
            logger.warning(f"Transaction {self.transaction_reference} not found for Flutterwave webhook")
            return HttpResponse("Transaction not found", status=404)
        except Exception as e:
            logger.error(f"Error processing Flutterwave failed charge: {str(e)}")
            return HttpResponse("Error processing webhook", status=500)
    
    def handle_successful_transfer(self):
        """Handle a successful transfer event"""
        # Implementation for handling merchant payouts
        return HttpResponse("Transfer webhook processed", status=200)
    
    def handle_failed_transfer(self):
        """Handle a failed transfer event"""
        # Implementation for handling failed merchant payouts
        return HttpResponse("Failed transfer webhook processed", status=200)


def handle_webhook(request, provider):
    """Factory function to get the appropriate webhook handler"""
    handlers = {
        'paystack': PaystackWebhookHandler,
        'flutterwave': FlutterwaveWebhookHandler
    }
    
    handler_class = handlers.get(provider.lower())
    if not handler_class:
        logger.error(f"Unsupported webhook provider: {provider}")
        return HttpResponse("Unsupported provider", status=400)
    
    handler = handler_class(request)
    return handler.process()

import json
import hmac
import hashlib
import logging
import datetime
from django.conf import settings
from django.utils import timezone

from .models import Transaction, Merchant, Subscription, PaymentPlan, Customer
from .models import AnalyticsData, PaymentMethodStats

logger = logging.getLogger(__name__)

class WebhookHandler:
    """
    Handles webhooks from various payment providers
    """
    
    @classmethod
    def process_webhook(cls, provider, request, payload=None):
        """
        Process incoming webhooks based on the provider
        """
        if provider == 'paystack':
            return cls.process_paystack_webhook(request)
        elif provider == 'flutterwave':
            return cls.process_flutterwave_webhook(request)
        elif provider == 'stripe':
            return cls.process_stripe_webhook(request)
        else:
            logger.warning(f"Unsupported webhook provider: {provider}")
            return False, "Unsupported webhook provider"
    
    @staticmethod
    def process_paystack_webhook(request):
        """
        Process webhooks from Paystack
        
        Supports:
        - charge.success: Payment completed successfully 
        - transfer.success: Payout to merchant completed
        - subscription.create: New subscription created
        - subscription.disable: Subscription disabled/cancelled
        - invoice.payment_failed: Subscription payment failed
        """
        # Verify webhook signature (in production)
        signature = request.META.get('HTTP_X_PAYSTACK_SIGNATURE')
        if not signature:
            logger.warning("Missing Paystack signature header")
            return False, "Missing signature header"
        
        secret = settings.PAYSTACK_SECRET_KEY
        computed_signature = hmac.new(
            secret.encode('utf-8'),
            request.body,
            hashlib.sha512
        ).hexdigest()
        
        if signature != computed_signature:
            logger.warning("Invalid Paystack webhook signature")
            return False, "Invalid signature"
        
        # Parse webhook payload
        try:
            payload = json.loads(request.body)
            event_type = payload.get('event')
            data = payload.get('data', {})
            
            # Process different event types
            if event_type == 'charge.success':
                return WebhookHandler._handle_successful_payment(data, 'paystack')
            elif event_type == 'transfer.success':
                return WebhookHandler._handle_successful_payout(data, 'paystack')
            elif event_type == 'subscription.create':
                return WebhookHandler._handle_subscription_creation(data, 'paystack')
            elif event_type == 'subscription.disable':
                return WebhookHandler._handle_subscription_cancellation(data, 'paystack')
            elif event_type == 'invoice.payment_failed':
                return WebhookHandler._handle_failed_payment(data, 'paystack')
            else:
                logger.info(f"Unhandled Paystack event type: {event_type}")
                return True, "Event received but not processed"
                
        except json.JSONDecodeError:
            logger.error("Failed to decode Paystack webhook payload")
            return False, "Invalid webhook payload"
        except Exception as e:
            logger.error(f"Error processing Paystack webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def process_flutterwave_webhook(request):
        """
        Process webhooks from Flutterwave
        
        Supports:
        - charge.completed: Payment completed
        - transfer.completed: Payout to merchant completed 
        - subscription.cancelled: Subscription cancelled
        - subscription.created: New subscription created
        """
        # Verify webhook signature (in production)
        signature = request.META.get('HTTP_VERIF_HASH')
        if not signature:
            logger.warning("Missing Flutterwave verification hash")
            return False, "Missing verification hash"
        
        secret = settings.FLUTTERWAVE_SECRET_HASH
        if signature != secret:
            logger.warning("Invalid Flutterwave webhook verification hash")
            return False, "Invalid verification hash"
        
        # Parse webhook payload
        try:
            payload = json.loads(request.body)
            event_type = payload.get('event', '')
            data = payload.get('data', {})
            
            # Process different event types
            if event_type == 'charge.completed':
                return WebhookHandler._handle_successful_payment(data, 'flutterwave')
            elif event_type == 'transfer.completed':
                return WebhookHandler._handle_successful_payout(data, 'flutterwave')
            elif event_type == 'subscription.created':
                return WebhookHandler._handle_subscription_creation(data, 'flutterwave')
            elif event_type == 'subscription.cancelled':
                return WebhookHandler._handle_subscription_cancellation(data, 'flutterwave')
            elif event_type == 'payment.failed':
                return WebhookHandler._handle_failed_payment(data, 'flutterwave')
            else:
                logger.info(f"Unhandled Flutterwave event type: {event_type}")
                return True, "Event received but not processed"
                
        except json.JSONDecodeError:
            logger.error("Failed to decode Flutterwave webhook payload")
            return False, "Invalid webhook payload"
        except Exception as e:
            logger.error(f"Error processing Flutterwave webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def process_stripe_webhook(request):
        """
        Process webhooks from Stripe
        
        Supports:
        - payment_intent.succeeded: Payment completed successfully
        - payment_intent.payment_failed: Payment failed
        - payout.paid: Payout to merchant completed
        - customer.subscription.created: New subscription created
        - customer.subscription.deleted: Subscription cancelled
        - invoice.payment_failed: Subscription payment failed
        """
        # Verify webhook signature (in production)
        signature = request.META.get('HTTP_STRIPE_SIGNATURE')
        if not signature:
            logger.warning("Missing Stripe signature header")
            return False, "Missing signature header"
        
        # In production, validate the signature
        # stripe.Webhook.construct_event(request.body, signature, settings.STRIPE_WEBHOOK_SECRET)
        
        # Parse webhook payload
        try:
            payload = json.loads(request.body)
            event_type = payload.get('type', '')
            data = payload.get('data', {}).get('object', {})
            
            # Process different event types
            if event_type == 'payment_intent.succeeded':
                return WebhookHandler._handle_successful_payment(data, 'stripe')
            elif event_type == 'payment_intent.payment_failed':
                return WebhookHandler._handle_failed_payment(data, 'stripe')
            elif event_type == 'payout.paid':
                return WebhookHandler._handle_successful_payout(data, 'stripe')
            elif event_type == 'customer.subscription.created':
                return WebhookHandler._handle_subscription_creation(data, 'stripe')
            elif event_type == 'customer.subscription.deleted':
                return WebhookHandler._handle_subscription_cancellation(data, 'stripe')
            elif event_type == 'invoice.payment_failed':
                return WebhookHandler._handle_failed_payment(data, 'stripe')
            else:
                logger.info(f"Unhandled Stripe event type: {event_type}")
                return True, "Event received but not processed"
                
        except json.JSONDecodeError:
            logger.error("Failed to decode Stripe webhook payload")
            return False, "Invalid webhook payload"
        except Exception as e:
            logger.error(f"Error processing Stripe webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def _handle_successful_payment(data, provider):
        """
        Handle successful payment notifications
        """
        try:
            reference = None
            if provider == 'paystack':
                reference = data.get('reference')
            elif provider == 'flutterwave':
                reference = data.get('tx_ref')
            elif provider == 'stripe':
                metadata = data.get('metadata', {})
                reference = metadata.get('reference')
            
            if not reference:
                logger.error(f"No reference found in {provider} payment webhook")
                return False, "No transaction reference found"
            
            # Find and update the transaction
            try:
                transaction = Transaction.objects.get(reference=reference)
                
                # Update transaction details
                transaction.status = 'success'
                
                # Set payment method based on provider data
                if provider == 'paystack':
                    transaction.payment_method = data.get('channel', 'card')
                elif provider == 'flutterwave':
                    transaction.payment_method = data.get('payment_type', 'card')
                elif provider == 'stripe':
                    payment_method_details = data.get('charges', {}).get('data', [{}])[0].get('payment_method_details', {})
                    transaction.payment_method = payment_method_details.get('type', 'card')
                
                transaction.save()
                
                # Update analytics data
                WebhookHandler._update_analytics_data(transaction)
                
                # Handle subscription if this is a subscription payment
                WebhookHandler._check_and_update_subscription(transaction)
                
                logger.info(f"Successfully processed {provider} payment webhook for transaction {reference}")
                return True, "Payment processed successfully"
                
            except Transaction.DoesNotExist:
                logger.error(f"No transaction found with reference {reference} for {provider} webhook")
                return False, "Transaction not found"
                
        except Exception as e:
            logger.error(f"Error handling {provider} successful payment webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def _handle_failed_payment(data, provider):
        """
        Handle payment failure notifications
        """
        try:
            reference = None
            if provider == 'paystack':
                reference = data.get('reference')
            elif provider == 'flutterwave':
                reference = data.get('tx_ref')
            elif provider == 'stripe':
                metadata = data.get('metadata', {})
                reference = metadata.get('reference')
            
            if not reference:
                logger.error(f"No reference found in {provider} payment failure webhook")
                return False, "No transaction reference found"
            
            # Find and update the transaction
            try:
                transaction = Transaction.objects.get(reference=reference)
                
                # Update transaction details
                transaction.status = 'failed'
                
                # Record failure reason if available
                metadata = transaction.get_metadata() or {}
                
                if provider == 'paystack':
                    metadata['failure_reason'] = data.get('gateway_response', 'Unknown error')
                elif provider == 'flutterwave':
                    metadata['failure_reason'] = data.get('status', 'Unknown error')
                elif provider == 'stripe':
                    last_payment_error = data.get('last_payment_error', {})
                    metadata['failure_reason'] = last_payment_error.get('message', 'Unknown error')
                
                transaction.set_metadata(metadata)
                transaction.save()
                
                # Update analytics data
                WebhookHandler._update_analytics_data(transaction)
                
                logger.info(f"Successfully processed {provider} failed payment webhook for transaction {reference}")
                return True, "Failed payment recorded"
                
            except Transaction.DoesNotExist:
                logger.error(f"No transaction found with reference {reference} for {provider} webhook")
                return False, "Transaction not found"
                
        except Exception as e:
            logger.error(f"Error handling {provider} failed payment webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def _handle_successful_payout(data, provider):
        """
        Handle successful payout notifications (transfers to merchants)
        """
        # In a real implementation, this would update payout records
        logger.info(f"Successful payout via {provider} processed")
        return True, "Payout processed successfully"
    
    @staticmethod
    def _handle_subscription_creation(data, provider):
        """
        Handle subscription creation notifications
        """
        try:
            # Extract subscription data based on provider
            email = None
            plan_code = None
            subscription_code = None
            
            if provider == 'paystack':
                email = data.get('customer', {}).get('email')
                plan_code = data.get('plan', {}).get('plan_code')
                subscription_code = data.get('subscription_code')
            elif provider == 'flutterwave':
                email = data.get('customer', {}).get('email')
                plan_code = data.get('plan', '')
                subscription_code = data.get('id', '')
            elif provider == 'stripe':
                customer_id = data.get('customer', '')
                plan_code = data.get('plan', {}).get('id', '')
                subscription_code = data.get('id', '')
                # In Stripe, we'd need to fetch the email separately
                email = "example@example.com"  # Placeholder
            
            if not email or not plan_code or not subscription_code:
                logger.error(f"Missing required subscription data in {provider} webhook")
                return False, "Missing required subscription data"
            
            # Find customer
            try:
                customer = Customer.objects.get(email=email)
            except Customer.DoesNotExist:
                logger.error(f"No customer found with email {email} for {provider} subscription webhook")
                return False, "Customer not found"
            
            # Find payment plan
            try:
                # In a real implementation, we'd have a field to store provider-specific plan IDs
                # This is a simplification
                plan = PaymentPlan.objects.filter(name__icontains=plan_code).first()
                if not plan:
                    logger.error(f"No plan found matching {plan_code} for {provider} subscription webhook")
                    return False, "Plan not found"
            except Exception as e:
                logger.error(f"Error finding plan for {provider} subscription webhook: {str(e)}")
                return False, f"Error finding plan: {str(e)}"
            
            # Create or update subscription
            subscription, created = Subscription.objects.update_or_create(
                external_reference=subscription_code,
                defaults={
                    'customer': customer,
                    'plan': plan,
                    'status': 'active',
                    'next_payment_date': timezone.now() + datetime.timedelta(days=30),  # Simplified
                    'provider': provider
                }
            )
            
            logger.info(f"Successfully processed {provider} subscription creation webhook")
            return True, "Subscription created successfully"
            
        except Exception as e:
            logger.error(f"Error handling {provider} subscription creation webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def _handle_subscription_cancellation(data, provider):
        """
        Handle subscription cancellation notifications
        """
        try:
            subscription_code = None
            
            if provider == 'paystack':
                subscription_code = data.get('subscription_code')
            elif provider == 'flutterwave':
                subscription_code = data.get('id', '')
            elif provider == 'stripe':
                subscription_code = data.get('id', '')
            
            if not subscription_code:
                logger.error(f"No subscription code found in {provider} cancellation webhook")
                return False, "No subscription code found"
            
            # Find and update subscription
            try:
                subscription = Subscription.objects.get(external_reference=subscription_code)
                
                # Update subscription status
                subscription.status = 'cancelled'
                subscription.cancelled_at = timezone.now()
                subscription.save()
                
                logger.info(f"Successfully processed {provider} subscription cancellation webhook")
                return True, "Subscription cancelled successfully"
                
            except Subscription.DoesNotExist:
                logger.error(f"No subscription found with code {subscription_code} for {provider} webhook")
                return False, "Subscription not found"
                
        except Exception as e:
            logger.error(f"Error handling {provider} subscription cancellation webhook: {str(e)}")
            return False, f"Error: {str(e)}"
    
    @staticmethod
    def _check_and_update_subscription(transaction):
        """
        Check if this transaction is for a subscription and update as needed
        """
        metadata = transaction.get_metadata() or {}
        subscription_code = metadata.get('subscription_code')
        
        if subscription_code:
            try:
                subscription = Subscription.objects.get(external_reference=subscription_code)
                
                # Calculate next payment date based on plan interval
                next_payment_date = timezone.now()
                if subscription.plan.interval == 'daily':
                    next_payment_date += datetime.timedelta(days=1)
                elif subscription.plan.interval == 'weekly':
                    next_payment_date += datetime.timedelta(days=7)
                elif subscription.plan.interval == 'monthly':
                    next_payment_date += datetime.timedelta(days=30)
                elif subscription.plan.interval == 'quarterly':
                    next_payment_date += datetime.timedelta(days=90)
                elif subscription.plan.interval == 'annually':
                    next_payment_date += datetime.timedelta(days=365)
                
                # Update subscription
                subscription.last_payment_date = timezone.now()
                subscription.next_payment_date = next_payment_date
                subscription.save()
                
                logger.info(f"Updated subscription {subscription_code} after successful payment")
            except Subscription.DoesNotExist:
                logger.warning(f"No subscription found with code {subscription_code}")
    
    @staticmethod
    def _update_analytics_data(transaction):
        """
        Update analytics data based on transaction
        """
        merchant = transaction.merchant
        date = transaction.created_at.date()
        currency = transaction.currency
        
        # Update or create daily analytics data
        analytics, created = AnalyticsData.objects.get_or_create(
            merchant=merchant,
            date=date,
            currency=currency,
            defaults={
                'total_transactions': 0,
                'successful_transactions': 0,
                'failed_transactions': 0,
                'total_volume': 0,
                'new_customers': 0
            }
        )
        
        # Update metrics
        analytics.total_transactions += 1
        
        if transaction.status == 'success':
            analytics.successful_transactions += 1
            analytics.total_volume += float(transaction.amount)
        elif transaction.status == 'failed':
            analytics.failed_transactions += 1
        
        # Check if this is the customer's first transaction today
        if transaction.customer:
            customer_transactions = Transaction.objects.filter(
                customer=transaction.customer,
                created_at__date__lt=date
            ).exists()
            
            if not customer_transactions:
                analytics.new_customers += 1
        
        analytics.save()
        
        # Update payment method stats if successful
        if transaction.status == 'success' and transaction.payment_method:
            method_stats, created = PaymentMethodStats.objects.get_or_create(
                merchant=merchant,
                date=date,
                payment_method=transaction.payment_method,
                defaults={
                    'transaction_count': 0,
                    'volume': 0
                }
            )
            
            method_stats.transaction_count += 1
            method_stats.volume += float(transaction.amount)
            method_stats.save()

"""
Webhook Service for Payment Gateway

This module handles incoming webhook notifications from payment providers.
It verifies webhook signatures, processes various event types, and updates
transactions accordingly.
"""

import json
import hmac
import hashlib
import logging
import time
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Import our fraud detection service
from .fraud_detector import analyze_transaction

class WebhookProcessor:
    """Base class for webhook processing"""
    
    def __init__(self, request_data, headers=None, signature=None):
        self.request_data = request_data
        self.headers = headers or {}
        self.signature = signature
        self.event_data = None
    
    def process(self):
        """
        Process the webhook
        
        Returns:
            tuple: (is_valid, event_type, transaction_reference, processed_data)
        """
        # Verify webhook signature
        if not self.verify_signature():
            logger.warning("Invalid webhook signature")
            return False, None, None, None
        
        # Parse the webhook data
        if not self.parse_data():
            logger.warning("Failed to parse webhook data")
            return False, None, None, None
        
        # Process based on event type
        event_type = self.get_event_type()
        if not event_type:
            logger.warning("Unknown webhook event type")
            return False, None, None, None
        
        # Get transaction reference
        transaction_ref = self.get_transaction_reference()
        
        # Process event type-specific data
        processed_data = self.process_event(event_type)
        
        return True, event_type, transaction_ref, processed_data
    
    def verify_signature(self):
        """
        Verify webhook signature
        
        Returns:
            bool: True if signature is valid, False otherwise
        """
        # Base class always returns True
        # Subclasses should implement provider-specific verification
        return True
    
    def parse_data(self):
        """
        Parse webhook data into Python objects
        
        Returns:
            bool: True if parsing successful, False otherwise
        """
        try:
            if isinstance(self.request_data, bytes):
                self.event_data = json.loads(self.request_data.decode('utf-8'))
            elif isinstance(self.request_data, str):
                self.event_data = json.loads(self.request_data)
            else:
                self.event_data = self.request_data
            return True
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to parse webhook data: {str(e)}")
            return False
    
    def get_event_type(self):
        """
        Get event type from webhook data
        
        Returns:
            str: Event type or None
        """
        # Base implementation returns None
        # Subclasses should implement provider-specific logic
        return None
    
    def get_transaction_reference(self):
        """
        Get transaction reference from webhook data
        
        Returns:
            str: Transaction reference or None
        """
        # Base implementation returns None
        # Subclasses should implement provider-specific logic
        return None
    
    def process_event(self, event_type):
        """
        Process specific event type
        
        Args:
            event_type: The event type to process
            
        Returns:
            dict: Processed data
        """
        # Base implementation returns empty dict
        # Subclasses should implement provider-specific logic
        return {}


class StripeWebhookProcessor(WebhookProcessor):
    """Processor for Stripe webhooks"""
    
    def verify_signature(self):
        """Verify Stripe webhook signature"""
        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')
        if not webhook_secret or 'Stripe-Signature' not in self.headers:
            return False
        
        stripe_signature = self.headers.get('Stripe-Signature', '')
        
        try:
            # Get timestamp and signatures from header
            header_items = dict(item.split('=') for item in stripe_signature.split(','))
            timestamp = header_items.get('t')
            signature = header_items.get('v1')
            
            if not timestamp or not signature:
                return False
            
            # Prepare signed payload
            signed_payload = f"{timestamp}.{self.request_data.decode('utf-8')}"
            computed_signature = hmac.new(
                webhook_secret.encode('utf-8'),
                signed_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Check if signatures match
            return hmac.compare_digest(computed_signature, signature)
        except Exception as e:
            logger.error(f"Stripe signature verification failed: {str(e)}")
            return False
    
    def get_event_type(self):
        """Get event type from Stripe webhook"""
        if not self.event_data or 'type' not in self.event_data:
            return None
        return self.event_data.get('type')
    
    def get_transaction_reference(self):
        """Get transaction reference from Stripe webhook"""
        if not self.event_data or 'data' not in self.event_data:
            return None
        
        data = self.event_data.get('data', {}).get('object', {})
        
        # Try to find reference in different locations based on event type
        event_type = self.get_event_type()
        
        if event_type.startswith('payment_intent'):
            # Check metadata first (our reference)
            if 'metadata' in data and 'transaction_reference' in data['metadata']:
                return data['metadata']['transaction_reference']
            # Fallback to Stripe's ID
            return data.get('id')
        
        elif event_type.startswith('charge'):
            # Check metadata first
            if 'metadata' in data and 'transaction_reference' in data['metadata']:
                return data['metadata']['transaction_reference']
            # Check payment intent metadata
            payment_intent = data.get('payment_intent')
            if payment_intent and isinstance(payment_intent, dict) and 'metadata' in payment_intent:
                if 'transaction_reference' in payment_intent['metadata']:
                    return payment_intent['metadata']['transaction_reference']
            # Fallback to Stripe's ID
            return data.get('id')
        
        # Default fallback
        if 'metadata' in data and 'transaction_reference' in data['metadata']:
            return data['metadata']['transaction_reference']
        
        return None
    
    def process_event(self, event_type):
        """Process Stripe event"""
        if not self.event_data or 'data' not in self.event_data:
            return {}
        
        data = self.event_data.get('data', {}).get('object', {})
        result = {
            'provider': 'stripe',
            'event_type': event_type,
            'raw_data': self.event_data
        }
        
        # Process based on event type
        if event_type == 'payment_intent.succeeded':
            result.update({
                'status': 'success',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'payment_method': data.get('payment_method_types', ['card'])[0],
                'provider_fee': data.get('application_fee_amount'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'payment_intent.payment_failed':
            result.update({
                'status': 'failed',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'error': data.get('last_payment_error', {}).get('message', 'Payment failed'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'charge.succeeded':
            result.update({
                'status': 'success',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'payment_method': data.get('payment_method_details', {}).get('type'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'charge.failed':
            result.update({
                'status': 'failed',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'error': data.get('failure_message', 'Charge failed'),
                'metadata': data.get('metadata', {})
            })
        
        return result


class PaystackWebhookProcessor(WebhookProcessor):
    """Processor for Paystack webhooks"""
    
    def verify_signature(self):
        """Verify Paystack webhook signature"""
        webhook_secret = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
        if not webhook_secret or 'X-Paystack-Signature' not in self.headers:
            return False
        
        signature = self.headers.get('X-Paystack-Signature')
        
        try:
            # Calculate HMAC SHA512
            computed_signature = hmac.new(
                webhook_secret.encode('utf-8'),
                self.request_data,
                hashlib.sha512
            ).hexdigest()
            
            # Compare signatures
            return hmac.compare_digest(computed_signature, signature)
        except Exception as e:
            logger.error(f"Paystack signature verification failed: {str(e)}")
            return False
    
    def get_event_type(self):
        """Get event type from Paystack webhook"""
        if not self.event_data or 'event' not in self.event_data:
            return None
        return self.event_data.get('event')
    
    def get_transaction_reference(self):
        """Get transaction reference from Paystack webhook"""
        if not self.event_data or 'data' not in self.event_data:
            return None
        
        data = self.event_data.get('data', {})
        
        # Try to get our reference from metadata
        metadata = data.get('metadata', {})
        if metadata and 'transaction_reference' in metadata:
            return metadata['transaction_reference']
        
        # Fallback to Paystack's reference
        return data.get('reference')
    
    def process_event(self, event_type):
        """Process Paystack event"""
        if not self.event_data or 'data' not in self.event_data:
            return {}
        
        data = self.event_data.get('data', {})
        result = {
            'provider': 'paystack',
            'event_type': event_type,
            'raw_data': self.event_data
        }
        
        # Process based on event type
        if event_type == 'charge.success':
            result.update({
                'status': 'success',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'payment_method': data.get('authorization', {}).get('channel', 'card'),
                'provider_fee': data.get('fees'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'charge.failed':
            result.update({
                'status': 'failed',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'error': data.get('gateway_response', 'Payment failed'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'subscription.create':
            result.update({
                'status': 'active',
                'subscription_code': data.get('subscription_code'),
                'customer_email': data.get('customer', {}).get('email'),
                'plan_code': data.get('plan', {}).get('plan_code'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'subscription.disable':
            result.update({
                'status': 'cancelled',
                'subscription_code': data.get('subscription_code'),
                'customer_email': data.get('customer', {}).get('email'),
                'metadata': data.get('metadata', {})
            })
        
        return result


class FlutterwaveWebhookProcessor(WebhookProcessor):
    """Processor for Flutterwave webhooks"""
    
    def verify_signature(self):
        """Verify Flutterwave webhook signature"""
        webhook_secret = getattr(settings, 'FLUTTERWAVE_SECRET_HASH', '')
        if not webhook_secret or 'verif-hash' not in self.headers:
            return False
        
        received_hash = self.headers.get('verif-hash')
        
        # Simple hash comparison - Flutterwave just sends a predefined hash
        return hmac.compare_digest(webhook_secret, received_hash)
    
    def get_event_type(self):
        """Get event type from Flutterwave webhook"""
        if not self.event_data or 'event' not in self.event_data:
            return None
        return self.event_data.get('event')
    
    def get_transaction_reference(self):
        """Get transaction reference from Flutterwave webhook"""
        if not self.event_data or 'data' not in self.event_data:
            return None
        
        data = self.event_data.get('data', {})
        
        # Try to get our reference from meta
        meta = data.get('meta', {})
        if meta and 'transaction_reference' in meta:
            return meta['transaction_reference']
        
        # Fallback to transaction reference or ID
        return data.get('tx_ref') or data.get('id')
    
    def process_event(self, event_type):
        """Process Flutterwave event"""
        if not self.event_data or 'data' not in self.event_data:
            return {}
        
        data = self.event_data.get('data', {})
        result = {
            'provider': 'flutterwave',
            'event_type': event_type,
            'raw_data': self.event_data
        }
        
        # Process based on event type
        if event_type == 'charge.completed':
            result.update({
                'status': 'success' if data.get('status') == 'successful' else 'pending',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'payment_method': data.get('payment_type', 'card'),
                'provider_fee': data.get('app_fee'),
                'metadata': data.get('meta', {})
            })
            
        elif event_type == 'charge.failed':
            result.update({
                'status': 'failed',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'error': data.get('processor_response', 'Payment failed'),
                'metadata': data.get('meta', {})
            })
            
        elif event_type == 'subscription.created':
            result.update({
                'status': 'active',
                'subscription_id': data.get('id'),
                'customer_email': data.get('customer', {}).get('email'),
                'plan_id': data.get('plan'),
                'metadata': data.get('meta', {})
            })
            
        elif event_type == 'subscription.cancelled':
            result.update({
                'status': 'cancelled',
                'subscription_id': data.get('id'),
                'customer_email': data.get('customer', {}).get('email'),
                'metadata': data.get('meta', {})
            })
        
        return result


class StripeWebhookProcessor(WebhookProcessor):
    """Processor for Stripe webhooks"""
    
    def verify_signature(self):
        """Verify Stripe webhook signature"""
        webhook_secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')
        if not webhook_secret or 'Stripe-Signature' not in self.headers:
            return False
        
        stripe_signature = self.headers.get('Stripe-Signature', '')
        
        try:
            # Get timestamp and signatures from header
            header_items = dict(item.split('=') for item in stripe_signature.split(','))
            timestamp = header_items.get('t')
            signature = header_items.get('v1')
            
            if not timestamp or not signature:
                return False
            
            # Prepare signed payload
            signed_payload = f"{timestamp}.{self.request_data.decode('utf-8')}"
            computed_signature = hmac.new(
                webhook_secret.encode('utf-8'),
                signed_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Check if signatures match
            return hmac.compare_digest(computed_signature, signature)
        except Exception as e:
            logger.error(f"Stripe signature verification failed: {str(e)}")
            return False
    
    def get_event_type(self):
        """Get event type from Stripe webhook"""
        if not self.event_data or 'type' not in self.event_data:
            return None
        return self.event_data.get('type')
    
    def get_transaction_reference(self):
        """Get transaction reference from Stripe webhook"""
        if not self.event_data or 'data' not in self.event_data:
            return None
        
        data = self.event_data.get('data', {}).get('object', {})
        
        # Try to find reference in different locations based on event type
        event_type = self.get_event_type()
        
        if event_type and event_type.startswith('payment_intent'):
            # Check metadata first (our reference)
            if 'metadata' in data and 'transaction_reference' in data['metadata']:
                return data['metadata']['transaction_reference']
            # Fallback to Stripe's ID
            return data.get('id')
        
        elif event_type and event_type.startswith('charge'):
            # Check metadata first
            if 'metadata' in data and 'transaction_reference' in data['metadata']:
                return data['metadata']['transaction_reference']
            # Check payment intent metadata
            payment_intent = data.get('payment_intent')
            if payment_intent and isinstance(payment_intent, dict) and 'metadata' in payment_intent:
                if 'transaction_reference' in payment_intent['metadata']:
                    return payment_intent['metadata']['transaction_reference']
            # Fallback to Stripe's ID
            return data.get('id')
        
        # Default fallback
        if 'metadata' in data and 'transaction_reference' in data['metadata']:
            return data['metadata']['transaction_reference']
        
        return None
    
    def process_event(self, event_type):
        """Process Stripe event"""
        if not self.event_data or 'data' not in self.event_data:
            return {}
        
        data = self.event_data.get('data', {}).get('object', {})
        result = {
            'provider': 'stripe',
            'event_type': event_type,
            'raw_data': self.event_data
        }
        
        # Process based on event type
        if event_type == 'payment_intent.succeeded':
            result.update({
                'status': 'success',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'payment_method': data.get('payment_method_types', ['card'])[0],
                'provider_fee': data.get('application_fee_amount'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'payment_intent.payment_failed':
            result.update({
                'status': 'failed',
                'amount': data.get('amount'),
                'currency': data.get('currency', '').upper(),
                'error': data.get('last_payment_error', {}).get('message', 'Payment failed'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'customer.subscription.created':
            result.update({
                'status': 'active',
                'subscription_id': data.get('id'),
                'customer_id': data.get('customer'),
                'plan_id': data.get('plan', {}).get('id'),
                'metadata': data.get('metadata', {})
            })
            
        elif event_type == 'customer.subscription.deleted':
            result.update({
                'status': 'cancelled',
                'subscription_id': data.get('id'),
                'customer_id': data.get('customer'),
                'metadata': data.get('metadata', {})
            })
        
        return result


def process_webhook(request_data, headers=None, provider=None):
    """
    Process webhook data from payment providers
    
    Args:
        request_data: Raw webhook data (bytes or str)
        headers: HTTP headers (dict)
        provider: Provider name or None (auto-detect)
        
    Returns:
        dict: Processing result
    """
    headers = headers or {}
    provider_lower = provider.lower() if provider else None
    
    # Determine processor based on provider or headers
    if provider_lower == 'stripe' or 'Stripe-Signature' in headers:
        processor = StripeWebhookProcessor(request_data, headers)
    elif provider_lower == 'paystack' or 'X-Paystack-Signature' in headers:
        processor = PaystackWebhookProcessor(request_data, headers)
    elif provider_lower == 'flutterwave' or 'verif-hash' in headers:
        processor = FlutterwaveWebhookProcessor(request_data, headers)
    else:
        logger.error("Unknown webhook provider")
        return {
            'success': False,
            'error': 'Unknown provider'
        }
    
    # Process webhook
    is_valid, event_type, transaction_ref, processed_data = processor.process()
    
    if not is_valid:
        logger.warning("Invalid webhook received")
        return {
            'success': False,
            'error': 'Invalid webhook'
        }
    
    if not transaction_ref:
        logger.warning("No transaction reference found in webhook")
        return {
            'success': False,
            'error': 'No transaction reference'
        }
    
    # Update transaction in database
    try:
        success = update_transaction(transaction_ref, processed_data)
        return {
            'success': success,
            'transaction_reference': transaction_ref,
            'event_type': event_type
        }
    except Exception as e:
        logger.error(f"Failed to update transaction: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'transaction_reference': transaction_ref
        }


def update_transaction(transaction_reference, webhook_data):
    """
    Update transaction based on webhook data
    
    Args:
        transaction_reference: Transaction reference
        webhook_data: Processed webhook data
        
    Returns:
        bool: True if update successful, False otherwise
    """
    from .models import Transaction, AnalyticsData, PaymentMethodStats, Subscription, Customer, PaymentPlan
    
    # Find transaction by reference
    try:
        with transaction.atomic():
            try:
                tx = Transaction.objects.select_for_update().get(reference=transaction_reference)
            except Transaction.DoesNotExist:
                logger.error(f"Transaction not found: {transaction_reference}")
                return False
            
            # Don't update if transaction is already in final state
            if tx.status in ['success', 'failed', 'refunded']:
                if tx.status == webhook_data.get('status'):
                    # Same status, nothing to update
                    return True
                elif webhook_data.get('status') == 'refunded' and tx.status == 'success':
                    # Allow refund updates to successful transactions
                    pass
                else:
                    # Otherwise, log but don't update finalized transactions
                    logger.warning(
                        f"Received webhook for finalized transaction: {transaction_reference}. "
                        f"Current status: {tx.status}, Webhook status: {webhook_data.get('status')}"
                    )
                    return True
            
            # Update transaction status
            if 'status' in webhook_data:
                tx.status = webhook_data['status']
            
            # Update payment details if available
            if 'payment_method' in webhook_data:
                tx.payment_method = webhook_data['payment_method']
            
            # Update error message if failed
            if webhook_data.get('status') == 'failed' and 'error' in webhook_data:
                # Store error in metadata
                metadata = tx.get_metadata() or {}
                metadata['error_message'] = webhook_data['error']
                tx.set_metadata(metadata)
            
            # Update provider fee if available
            if 'provider_fee' in webhook_data and webhook_data['provider_fee']:
                tx.provider_fee = webhook_data['provider_fee']
            
            # Store raw webhook data in metadata
            metadata = tx.get_metadata() or {}
            if 'webhooks' not in metadata:
                metadata['webhooks'] = []
            
            webhook_entry = {
                'timestamp': timezone.now().isoformat(),
                'provider': webhook_data.get('provider'),
                'event_type': webhook_data.get('event_type')
            }
            
            metadata['webhooks'].append(webhook_entry)
            tx.set_metadata(metadata)
            
            # Save transaction
            tx.save()
            
            # If status changed to success, update analytics data
            if tx.status == 'success':
                update_analytics_data(tx)
                
                # If this is a subscription payment, update subscription status
                check_and_update_subscription(tx)
                
                # Send notification
                try:
                    send_success_notification(tx)
                except Exception as e:
                    logger.error(f"Failed to send success notification: {str(e)}")
            
            # If status changed to failed, send failure notification
            elif tx.status == 'failed':
                update_analytics_data(tx)
                
                # Send failure notification
                try:
                    send_failed_notification(tx)
                except Exception as e:
                    logger.error(f"Failed to send failure notification: {str(e)}")
            
            # Handle subscription-related webhook events
            event_type = webhook_data.get('event_type')
            if event_type in ['subscription.create', 'customer.subscription.created', 'subscription.created']:
                handle_subscription_creation(webhook_data)
            elif event_type in ['subscription.disable', 'customer.subscription.deleted', 'subscription.cancelled']:
                handle_subscription_cancellation(webhook_data)
            
            return True
            
    except Exception as e:
        logger.error(f"Failed to update transaction {transaction_reference}: {str(e)}")
        return False


def update_analytics_data(transaction):
    """
    Update analytics data based on transaction
    
    Args:
        transaction: Transaction object
    """
    from .models import AnalyticsData, PaymentMethodStats
    
    merchant = transaction.merchant
    if not merchant:
        return
        
    date = transaction.created_at.date()
    currency = transaction.currency
    
    # Update or create daily analytics data
    analytics, created = AnalyticsData.objects.get_or_create(
        merchant=merchant,
        date=date,
        currency=currency,
        defaults={
            'total_transactions': 0,
            'successful_transactions': 0,
            'failed_transactions': 0,
            'total_volume': 0,
            'new_customers': 0
        }
    )
    
    # Update metrics
    analytics.total_transactions += 1
    
    if transaction.status == 'success':
        analytics.successful_transactions += 1
        analytics.total_volume += float(transaction.amount)
    elif transaction.status == 'failed':
        analytics.failed_transactions += 1
    
    # Check if this is the customer's first transaction today
    if transaction.customer:
        from django.db.models import Count
        from .models import Transaction
        
        customer_transactions = Transaction.objects.filter(
            customer=transaction.customer,
            created_at__date__lt=date
        ).exists()
        
        if not customer_transactions:
            analytics.new_customers += 1
    
    analytics.save()
    
    # Update payment method stats if successful
    if transaction.status == 'success' and transaction.payment_method:
        method_stats, created = PaymentMethodStats.objects.get_or_create(
            merchant=merchant,
            date=date,
            payment_method=transaction.payment_method,
            defaults={
                'transaction_count': 0,
                'volume': 0
            }
        )
        
        method_stats.transaction_count += 1
        method_stats.volume += float(transaction.amount)
        method_stats.save()


def check_and_update_subscription(transaction):
    """
    Check if this transaction is for a subscription and update as needed
    
    Args:
        transaction: Transaction object
    """
    from .models import Subscription
    import datetime
    
    metadata = transaction.get_metadata() or {}
    subscription_code = metadata.get('subscription_code')
    
    if subscription_code:
        try:
            subscription = Subscription.objects.get(external_reference=subscription_code)
            
            # Calculate next payment date based on plan interval
            next_payment_date = timezone.now()
            if subscription.plan.interval == 'daily':
                next_payment_date += datetime.timedelta(days=1)
            elif subscription.plan.interval == 'weekly':
                next_payment_date += datetime.timedelta(days=7)
            elif subscription.plan.interval == 'monthly':
                next_payment_date += datetime.timedelta(days=30)
            elif subscription.plan.interval == 'quarterly':
                next_payment_date += datetime.timedelta(days=90)
            elif subscription.plan.interval == 'annually':
                next_payment_date += datetime.timedelta(days=365)
            
            # Update subscription
            subscription.last_payment_date = timezone.now()
            subscription.next_payment_date = next_payment_date
            subscription.save()
            
            logger.info(f"Updated subscription {subscription_code} after successful payment")
        except Subscription.DoesNotExist:
            logger.warning(f"No subscription found with code {subscription_code}")


def handle_subscription_creation(webhook_data):
    """
    Handle subscription creation webhook data
    
    Args:
        webhook_data: Processed webhook data
    """
    from .models import Customer, PaymentPlan, Subscription
    
    provider = webhook_data.get('provider')
    
    # Extract data based on provider
    if provider == 'paystack':
        customer_email = webhook_data.get('customer_email')
        subscription_code = webhook_data.get('subscription_code')
        plan_code = webhook_data.get('plan_code')
    elif provider == 'flutterwave':
        customer_email = webhook_data.get('customer_email')
        subscription_code = webhook_data.get('subscription_id')
        plan_code = webhook_data.get('plan_id')
    elif provider == 'stripe':
        # For stripe, we'd need to make an API call to get customer email from customer ID
        # For simplicity here, we'll just handle by ID
        customer_id = webhook_data.get('customer_id')
        subscription_code = webhook_data.get('subscription_id')
        plan_code = webhook_data.get('plan_id')
        
        # In a real implementation, you would:
        # customer_email = stripe.Customer.retrieve(customer_id).email
        customer_email = None
    else:
        logger.error(f"Unsupported provider for subscription creation: {provider}")
        return
    
    if not subscription_code:
        logger.error("No subscription code provided")
        return
    
    try:
        # Find customer
        customer = None
        if customer_email:
            try:
                customer = Customer.objects.get(email=customer_email)
            except Customer.DoesNotExist:
                logger.error(f"Customer not found with email {customer_email}")
                return
        
        # Find plan - in a real implementation, you'd store provider-specific plan IDs
        # This is a simplified example
        from django.db.models import Q
        plans = PaymentPlan.objects.filter(
            Q(name__icontains=plan_code) | Q(description__icontains=plan_code)
        )
        
        if not plans.exists():
            logger.error(f"No plan found matching: {plan_code}")
            return
            
        plan = plans.first()
        
        # Create or update subscription
        subscription, created = Subscription.objects.update_or_create(
            external_reference=subscription_code,
            defaults={
                'customer': customer,
                'plan': plan,
                'status': 'active',
                'provider': provider,
                'next_payment_date': timezone.now() + datetime.timedelta(days=30)  # Default to 30 days
            }
        )
        
        if created:
            logger.info(f"Created new subscription: {subscription_code}")
            
            # Send subscription confirmation email
            try:
                from .email_service import EmailService
                EmailService.send_subscription_confirmation(subscription)
                logger.info(f"Sent subscription confirmation email to: {customer.email}")
            except Exception as e:
                logger.error(f"Failed to send subscription confirmation email: {str(e)}")
                
        else:
            logger.info(f"Updated existing subscription: {subscription_code}")
        
    except Exception as e:
        logger.error(f"Failed to process subscription creation: {str(e)}")


def handle_subscription_cancellation(webhook_data):
    """
    Handle subscription cancellation webhook data
    
    Args:
        webhook_data: Processed webhook data
    """
    from .models import Subscription
    
    provider = webhook_data.get('provider')
    
    # Extract subscription code based on provider
    if provider == 'paystack':
        subscription_code = webhook_data.get('subscription_code')
    elif provider == 'flutterwave':
        subscription_code = webhook_data.get('subscription_id')
    elif provider == 'stripe':
        subscription_code = webhook_data.get('subscription_id')
    else:
        logger.error(f"Unsupported provider for subscription cancellation: {provider}")
        return
    
    if not subscription_code:
        logger.error("No subscription code provided")
        return
    
    try:
        # Find subscription
        try:
            subscription = Subscription.objects.get(external_reference=subscription_code)
        except Subscription.DoesNotExist:
            logger.error(f"Subscription not found: {subscription_code}")
            return
        
        # Update subscription
        subscription.status = 'cancelled'
        subscription.cancelled_at = timezone.now()
        subscription.save()
        
        logger.info(f"Cancelled subscription: {subscription_code}")
        
    except Exception as e:
        logger.error(f"Failed to process subscription cancellation: {str(e)}")


def send_success_notification(transaction):
    """
    Send notification when transaction is successful
    
    Args:
        transaction: Transaction object
    """
    # Send webhook to merchant callback URL if configured
    merchant = transaction.merchant
    if merchant and merchant.webhook_url:
        # In a real implementation, this would be a Celery task or background job
        try:
            logger.info(f"Sending webhook notification to merchant: {merchant.webhook_url}")
            
            # Actual notification logic would be here
            # requests.post(merchant.webhook_url, json={
            #    'event': 'transaction.success',
            #    'data': {
            #        'reference': transaction.reference,
            #        'amount': float(transaction.amount),
            #        'currency': transaction.currency,
            #        'status': transaction.status,
            #        'customer_email': transaction.email,
            #        'payment_method': transaction.payment_method,
            #        'created_at': transaction.created_at.isoformat()
            #    }
            # })
        except Exception as e:
            logger.error(f"Failed to send webhook to merchant: {str(e)}")
    
    # Send email notification to customer
    try:
        from .email_service import EmailService
        EmailService.send_transaction_success_notification(transaction)
        logger.info(f"Sent success email notification to customer: {transaction.email}")
    except Exception as e:
        logger.error(f"Failed to send email notification: {str(e)}")


def send_failed_notification(transaction):
    """
    Send notification when transaction fails
    
    Args:
        transaction: Transaction object
    """
    # Send webhook to merchant callback URL if configured
    merchant = transaction.merchant
    if merchant and merchant.webhook_url:
        # In a real implementation, this would be a Celery task or background job
        try:
            logger.info(f"Sending failed transaction webhook notification to merchant: {merchant.webhook_url}")
            
            # Actual webhook notification logic would be here
            # requests.post(merchant.webhook_url, json={
            #    'event': 'transaction.failed',
            #    'data': {
            #        'reference': transaction.reference,
            #        'amount': float(transaction.amount),
            #        'currency': transaction.currency,
            #        'status': transaction.status,
            #        'customer_email': transaction.email,
            #        'payment_method': transaction.payment_method,
            #        'created_at': transaction.created_at.isoformat(),
            #        'error': transaction.get_metadata().get('error_message') if transaction.get_metadata() else 'Unknown error'
            #    }
            # })
        except Exception as e:
            logger.error(f"Failed to send webhook to merchant: {str(e)}")
    
    # Send email notification to customer
    try:
        from .email_service import EmailService
        EmailService.send_transaction_failed_notification(transaction)
        logger.info(f"Sent failure email notification to customer: {transaction.email}")
    except Exception as e:
        logger.error(f"Failed to send email notification: {str(e)}")

import json
import hmac
import hashlib
import requests
import concurrent.futures
from datetime import datetime
from django.utils import timezone
from urllib.parse import urlparse
from .models import Webhook, Transaction, Merchant
import logging

logger = logging.getLogger(__name__)

class WebhookService:
    """Service class to handle all webhook related operations"""
    
    @staticmethod
    def generate_signature(payload, secret):
        """Generate a signature for the webhook payload"""
        payload_str = json.dumps(payload)
        signature = hmac.new(
            key=secret.encode('utf-8'),
            msg=payload_str.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()
        return signature
    
    @staticmethod
    def verify_signature(payload, signature, secret):
        """Verify that a webhook signature is valid"""
        expected_signature = WebhookService.generate_signature(payload, secret)
        return hmac.compare_digest(signature, expected_signature)
    
    @staticmethod
    def generate_webhook_payload(event_type, data):
        """Generate a standard webhook payload"""
        return {
            "event": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
            "hmskpy_event_id": f"evt_{hashlib.sha256(str(timezone.now().timestamp()).encode()).hexdigest()[:24]}"
        }
    
    @staticmethod
    def send_webhook(webhook, payload):
        """Send a webhook notification to the specified URL"""
        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "HamsukyPay-Webhook/1.0",
                "X-HamsukyPay-Signature": WebhookService.generate_signature(payload, webhook.secret)
            }
            
            response = requests.post(
                url=webhook.url,
                json=payload,
                headers=headers,
                timeout=10  # 10 second timeout
            )
            
            success = 200 <= response.status_code < 300
            
            # Update webhook stats
            if success:
                webhook.success_count += 1
            else:
                webhook.failure_count += 1
            
            webhook.last_triggered = timezone.now()
            webhook.save()
            
            # Log the webhook delivery attempt
            logger.info(f"Webhook {webhook.id} delivery to {webhook.url}: {'Success' if success else 'Failed'} with status {response.status_code}")
            
            return success, response.status_code, response.text[:1000] if response.text else ""
            
        except requests.RequestException as e:
            # Update webhook stats
            webhook.failure_count += 1
            webhook.last_triggered = timezone.now()
            webhook.save()
            
            logger.error(f"Webhook {webhook.id} delivery to {webhook.url} failed with exception: {str(e)}")
            
            return False, 0, str(e)
    
    @staticmethod
    def send_event_notification(merchant, event_type, data):
        """Send event notification to all webhooks registered for this event type"""
        if not merchant:
            return
            
        # Find all active webhooks for this merchant and event type
        webhooks = Webhook.objects.filter(
            merchant=merchant,
            event_type=event_type,
            status='active'
        )
        
        if not webhooks.exists():
            return
        
        # Generate the webhook payload once
        payload = WebhookService.generate_webhook_payload(event_type, data)
        
        # Send webhooks in parallel for better performance
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(WebhookService.send_webhook, webhook, payload): webhook
                for webhook in webhooks
            }
            
            for future in concurrent.futures.as_completed(futures):
                webhook = futures[future]
                try:
                    success, status_code, response_text = future.result()
                    
                    # If a webhook consistently fails, mark it as failed
                    if not success and webhook.failure_count >= 10 and webhook.success_rate < 30:
                        webhook.status = 'failed'
                        webhook.save()
                        logger.warning(f"Webhook {webhook.id} marked as failed due to consistent failures. Success rate: {webhook.success_rate}%")
                        
                except Exception as e:
                    logger.error(f"Error processing webhook {webhook.id}: {str(e)}")
    
    @staticmethod
    def handle_transaction_event(transaction, event_type):
        """Handle webhook notifications for transaction events"""
        if not transaction or not transaction.merchant:
            return
            
        # Prepare the data payload for the transaction event
        data = {
            "id": transaction.id,
            "reference": transaction.reference,
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.status,
            "customer": {
                "email": transaction.email
            },
            "payment_method": transaction.payment_method,
            "payment_provider": transaction.payment_provider,
            "created_at": transaction.created_at.isoformat(),
            "completed_at": transaction.completed_at.isoformat() if transaction.completed_at else None,
            "metadata": transaction.get_metadata()
        }
        
        # Send the webhook notification
        WebhookService.send_event_notification(transaction.merchant, event_type, data)
    
    @staticmethod
    def test_webhook(webhook, custom_payload=None):
        """Send a test event to a webhook endpoint"""
        # Create a test payload if none provided
        if not custom_payload:
            custom_payload = {
                "test": True,
                "message": "This is a test webhook from HamsukyPay"
            }
            
        # Generate a test webhook payload
        payload = WebhookService.generate_webhook_payload("test.webhook", custom_payload)
        
        # Send the test webhook
        return WebhookService.send_webhook(webhook, payload)
    
    @staticmethod
    def verify_webhook_url(url):
        """Verify that a webhook URL is valid and publicly accessible"""
        # Basic URL validation
        try:
            parsed = urlparse(url)
            valid_scheme = parsed.scheme in ['http', 'https']
            valid_netloc = parsed.netloc and '.' in parsed.netloc  # Simple check for a domain
            
            # We shouldn't accept localhost or private IPs for production webhooks
            is_localhost = parsed.netloc in ['localhost', '127.0.0.1', '::1'] or parsed.netloc.startswith('192.168.') or parsed.netloc.startswith('10.')
            
            if not (valid_scheme and valid_netloc) or is_localhost:
                return False, "Invalid URL. Must be a public HTTPS URL."
                
            # For security, we should only allow HTTPS URLs in production
            if parsed.scheme != 'https':
                return False, "Only HTTPS URLs are allowed for security reasons."
                
            return True, "URL is valid"
            
        except Exception:
            return False, "Invalid URL format"
    
    @staticmethod
    def create_webhook(merchant, url, event_type, description=None):
        """Create a new webhook endpoint for a merchant"""
        # Verify the URL first
        is_valid, message = WebhookService.verify_webhook_url(url)
        if not is_valid:
            return None, message
            
        # Generate a webhook secret
        import secrets
        webhook_secret = secrets.token_hex(32)
        
        # Create the webhook
        try:
            webhook = Webhook.objects.create(
                merchant=merchant,
                url=url,
                event_type=event_type,
                description=description,
                secret=webhook_secret,
                status='active'
            )
            return webhook, "Webhook created successfully"
        except Exception as e:
            return None, str(e)