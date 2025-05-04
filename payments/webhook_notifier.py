"""
Webhook Notifier Service

This module handles sending webhook notifications to merchant endpoints whenever
important events occur in the payment system.
"""

import json
import hmac
import hashlib
import time
import uuid
import logging
import requests
from threading import Thread
from django.conf import settings
from django.utils import timezone
from django.db import models

logger = logging.getLogger(__name__)

# Webhook event types
EVENT_TYPES = {
    # Payment events
    'payment.pending': 'Payment pending',
    'payment.success': 'Payment successful',
    'payment.failed': 'Payment failed',
    'payment.refunded': 'Payment refunded',
    'payment.chargeback': 'Payment chargeback initiated',
    'payment.chargeback.resolved': 'Payment chargeback resolved',
    'payment.dispute': 'Payment disputed',
    'payment.dispute.resolved': 'Payment dispute resolved',
    
    # Subscription events
    'subscription.created': 'Subscription created',
    'subscription.activated': 'Subscription activated',
    'subscription.payment.success': 'Subscription payment successful',
    'subscription.payment.failed': 'Subscription payment failed',
    'subscription.cancelled': 'Subscription cancelled',
    'subscription.paused': 'Subscription paused',
    'subscription.resumed': 'Subscription resumed',
    
    # Customer events
    'customer.created': 'Customer created',
    'customer.updated': 'Customer updated',
    'customer.card_added': 'Customer added a card',
    'customer.card_expired': 'Customer card expired',
    
    # Fraud events
    'fraud.high_risk': 'High risk transaction detected',
    'fraud.blacklisted': 'Blacklisted user attempted payment',
    
    # System events
    'system.test': 'Test webhook event'
}

class WebhookDelivery(models.Model):
    """Model to track webhook deliveries"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey('Merchant', on_delete=models.CASCADE, related_name='webhook_deliveries')
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    url = models.URLField()
    signature = models.CharField(max_length=255)
    status_code = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return f"{self.event_type} to {self.url} - {self.status_code or 'Pending'}"


class WebhookNotifier:
    """
    Service for sending webhook notifications to merchant endpoints
    """
    
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds
    
    @classmethod
    def send_webhook(cls, merchant, event_type, data, async_send=True):
        """
        Send a webhook notification to a merchant
        
        Args:
            merchant: The Merchant object to send the webhook to
            event_type: The type of event (payment.success, etc.)
            data: The data payload to send
            async_send: Whether to send asynchronously (default: True)
            
        Returns:
            WebhookDelivery: The created webhook delivery object
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Invalid event type: {event_type}")
        
        # Check if merchant has webhook URL configured
        if not merchant.webhook_url:
            logger.info(f"Merchant {merchant.id} has no webhook URL configured, skipping notification")
            return None
        
        # Validate URL format
        url = merchant.webhook_url
        if not url.startswith(('http://', 'https://')):
            logger.warning(f"Invalid webhook URL for merchant {merchant.id}: {url}")
            return None
        
        # Prepare webhook payload
        payload = {
            'event': event_type,
            'event_description': EVENT_TYPES.get(event_type, 'Unknown event'),
            'data': data,
            'merchant': str(merchant.id),
            'timestamp': int(time.time()),
            'id': str(uuid.uuid4())
        }
        
        # Generate signature
        signature = cls._generate_signature(merchant.secret_key, json.dumps(payload))
        
        # Create webhook delivery record
        delivery = WebhookDelivery.objects.create(
            merchant=merchant,
            event_type=event_type,
            payload=payload,
            url=url,
            signature=signature
        )
        
        # Send the webhook (async or sync)
        if async_send:
            Thread(
                target=cls._send_webhook_request,
                args=(delivery, payload, signature),
                daemon=True
            ).start()
        else:
            cls._send_webhook_request(delivery, payload, signature)
        
        return delivery
    
    @classmethod
    def _send_webhook_request(cls, delivery, payload, signature):
        """
        Send the actual HTTP request to the merchant's webhook URL
        
        Args:
            delivery: WebhookDelivery object
            payload: The webhook payload
            signature: The HMAC signature for the payload
        """
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'HamsukyPay-Webhook/1.0',
            'X-Hamsukypay-Signature': signature,
            'X-Hamsukypay-Event': delivery.event_type
        }
        
        # Make the request with retries
        attempts = 0
        success = False
        
        while attempts < cls.MAX_RETRIES and not success:
            attempts += 1
            delivery.attempts = attempts
            
            try:
                response = requests.post(
                    delivery.url,
                    json=payload,
                    headers=headers,
                    timeout=10  # 10-second timeout
                )
                
                # Update delivery record with response
                delivery.status_code = response.status_code
                delivery.response_body = response.text[:1000]  # Truncate long responses
                delivery.delivered_at = timezone.now()
                
                # Check if successful (HTTP 2xx)
                success = 200 <= response.status_code < 300
                
                if not success and attempts < cls.MAX_RETRIES:
                    # Sleep before retrying
                    time.sleep(cls.RETRY_DELAY)
                
            except requests.exceptions.RequestException as e:
                delivery.error = str(e)
                if attempts < cls.MAX_RETRIES:
                    time.sleep(cls.RETRY_DELAY)
        
        delivery.save()
        
        # Log the result
        if success:
            logger.info(f"Webhook {delivery.id} delivered successfully to {delivery.url}")
        else:
            logger.warning(
                f"Failed to deliver webhook {delivery.id} to {delivery.url} "
                f"after {attempts} attempts. Status: {delivery.status_code}"
            )
    
    @staticmethod
    def _generate_signature(secret_key, payload):
        """
        Generate an HMAC signature for the payload
        
        Args:
            secret_key: The merchant's secret key
            payload: The JSON payload as a string
            
        Returns:
            str: The signature
        """
        if isinstance(secret_key, str):
            secret_key = secret_key.encode()
            
        if isinstance(payload, str):
            payload = payload.encode()
            
        return hmac.new(
            secret_key,
            payload,
            hashlib.sha256
        ).hexdigest()


def payment_webhook_handler(sender, instance, **kwargs):
    """
    Signal handler for payment status changes
    
    Args:
        sender: The model class (Transaction)
        instance: The Transaction object
        created: Whether this is a new object
    """
    from .models import Transaction
    
    if not isinstance(instance, Transaction):
        return
        
    # Get previous status if this is an update
    previous_status = None
    if 'update_fields' in kwargs and 'status' in kwargs['update_fields']:
        previous_status = getattr(instance, '_previous_status', None)
        
    # Only trigger on status changes or new successful transactions
    if previous_status == instance.status:
        return
        
    # Get merchant
    merchant = instance.merchant
    if not merchant:
        return
        
    # Determine event type based on status
    event_type = None
    if instance.status == 'pending':
        event_type = 'payment.pending'
    elif instance.status == 'success':
        event_type = 'payment.success'
    elif instance.status == 'failed':
        event_type = 'payment.failed'
        
    if not event_type:
        return
        
    # Prepare data
    data = {
        'id': str(instance.id),
        'reference': instance.reference,
        'amount': float(instance.amount),
        'currency': instance.currency,
        'status': instance.status,
        'payment_method': instance.payment_method,
        'description': instance.description,
        'created_at': instance.created_at.isoformat(),
        'customer_email': instance.email
    }
    
    # Include customer data if available
    if instance.customer:
        data['customer'] = {
            'id': str(instance.customer.id),
            'email': instance.customer.email,
            'name': instance.customer.name
        }
        
    # Include metadata if available
    metadata = instance.get_metadata()
    if metadata:
        data['metadata'] = metadata
        
    # Send webhook notification
    try:
        WebhookNotifier.send_webhook(
            merchant=merchant,
            event_type=event_type,
            data=data
        )
    except Exception as e:
        logger.error(f"Error sending payment webhook: {str(e)}")
        

def subscription_webhook_handler(sender, instance, **kwargs):
    """
    Signal handler for subscription status changes
    
    Args:
        sender: The model class (Subscription)
        instance: The Subscription object
        created: Boolean indicating if this is a new instance
    """
    from .models import Subscription
    
    if not isinstance(instance, Subscription):
        return
        
    # Check if this is a new subscription
    created = kwargs.get('created', False)
    
    # Get merchant
    merchant = instance.plan.merchant
    if not merchant:
        return
        
    # Determine event type based on status and created flag
    event_type = None
    if created:
        event_type = 'subscription.created'
    elif instance.status == 'active':
        if getattr(instance, '_previous_status', None) != 'active':
            event_type = 'subscription.activated'
    elif instance.status == 'cancelled':
        event_type = 'subscription.cancelled'
    elif instance.status == 'paused':
        event_type = 'subscription.paused'
    elif instance.status == 'active' and getattr(instance, '_previous_status', None) == 'paused':
        event_type = 'subscription.resumed'
        
    if not event_type:
        return
        
    # Prepare data
    data = {
        'id': str(instance.id),
        'reference': instance.reference,
        'status': instance.status,
        'start_date': instance.start_date.isoformat(),
        'next_payment_date': instance.next_payment_date.isoformat() if instance.next_payment_date else None,
        'created_at': instance.created_at.isoformat(),
        'plan': {
            'id': str(instance.plan.id),
            'name': instance.plan.name,
            'amount': float(instance.plan.amount),
            'currency': instance.plan.currency,
            'interval': instance.plan.interval
        },
        'customer': {
            'id': str(instance.customer.id),
            'email': instance.customer.email,
            'name': instance.customer.name
        }
    }
    
    # Include metadata if available
    metadata = instance.get_metadata()
    if metadata:
        data['metadata'] = metadata
        
    # Send webhook notification
    try:
        WebhookNotifier.send_webhook(
            merchant=merchant,
            event_type=event_type,
            data=data
        )
    except Exception as e:
        logger.error(f"Error sending subscription webhook: {str(e)}")


def customer_webhook_handler(sender, instance, **kwargs):
    """
    Signal handler for customer changes
    
    Args:
        sender: The model class (Customer)
        instance: The Customer object
        created: Boolean indicating if this is a new instance
    """
    from .models import Customer
    
    if not isinstance(instance, Customer):
        return
        
    # Check if this is a new customer
    created = kwargs.get('created', False)
    
    # Get merchant
    merchant = instance.merchant
    if not merchant:
        return
        
    # Determine event type
    event_type = 'customer.created' if created else 'customer.updated'
        
    # Prepare data
    data = {
        'id': str(instance.id),
        'email': instance.email,
        'name': instance.name,
        'phone': instance.phone,
        'external_id': instance.external_id,
        'created_at': instance.created_at.isoformat()
    }
    
    # Include metadata if available
    metadata = instance.get_metadata()
    if metadata:
        data['metadata'] = metadata
        
    # Send webhook notification
    try:
        WebhookNotifier.send_webhook(
            merchant=merchant,
            event_type=event_type,
            data=data
        )
    except Exception as e:
        logger.error(f"Error sending customer webhook: {str(e)}")


def card_added_webhook_handler(sender, instance, **kwargs):
    """
    Signal handler for payment method changes
    
    Args:
        sender: The model class (PaymentMethod)
        instance: The PaymentMethod object
        created: Boolean indicating if this is a new instance
    """
    from .models import PaymentMethod
    
    if not isinstance(instance, PaymentMethod):
        return
        
    # Check if this is a new payment method and it's a card
    created = kwargs.get('created', False)
    if not created or instance.method_type != 'card':
        return
        
    # Get customer and merchant
    customer = instance.customer
    if not customer or not customer.merchant:
        return
        
    merchant = customer.merchant
        
    # Prepare data
    data = {
        'customer': {
            'id': str(customer.id),
            'email': customer.email,
            'name': customer.name
        },
        'card': {
            'reference': instance.reference,
            'last4': instance.last4,
            'card_type': instance.card_type,
            'exp_month': instance.exp_month,
            'exp_year': instance.exp_year,
            'is_default': instance.is_default
        }
    }
        
    # Send webhook notification
    try:
        WebhookNotifier.send_webhook(
            merchant=merchant,
            event_type='customer.card_added',
            data=data
        )
    except Exception as e:
        logger.error(f"Error sending card added webhook: {str(e)}")


def connect_webhook_signals():
    """
    Connect webhook signal handlers to model signals
    """
    from django.db.models.signals import post_save
    from .models import Transaction, Subscription, Customer, PaymentMethod
    
    # Connect transaction signals
    post_save.connect(payment_webhook_handler, sender=Transaction)
    
    # Connect subscription signals
    post_save.connect(subscription_webhook_handler, sender=Subscription)
    
    # Connect customer signals
    post_save.connect(customer_webhook_handler, sender=Customer)
    
    # Connect payment method signals
    post_save.connect(card_added_webhook_handler, sender=PaymentMethod)