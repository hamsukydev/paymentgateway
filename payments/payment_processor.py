import logging
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
from .models import Transaction, Customer, PaymentMethod
from .tokenization_service import TokenizationService
import uuid
import random
import string
import datetime
import json
import hashlib

logger = logging.getLogger(__name__)

class PaymentProviderException(Exception):
    """Exception raised for errors in payment provider interactions."""
    pass

"""
Standalone Payment Processor

This module handles all payment processing functionality directly within the application,
without relying on external payment gateways.
"""

from django.db import transaction as db_transaction

class StandalonePaymentProcessor:
    """
    Handles all payment processing functionality within the application.
    This is a standalone implementation that doesn't rely on external payment providers.
    """
    
    PAYMENT_METHODS = [
        'credit_card',
        'debit_card',
        'bank_transfer',
        'mobile_money',
        'ussd',
        'qr_code',
    ]
    
    CARD_TYPES = [
        'visa', 
        'mastercard',
        'amex',
        'discover',
        'verve',
    ]
    
    def __init__(self, merchant=None):
        self.merchant = merchant
        self.success_rate = getattr(settings, 'PAYMENT_SUCCESS_RATE', 0.95)
    
    def _generate_reference(self, length=8):
        """Generate a random alphanumeric reference"""
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(length))
    
    def initialize_payment(self, data):
        """
        Initialize a payment transaction
        
        Args:
            data: Dictionary containing payment details
                - amount: The payment amount
                - currency: The currency code (e.g., 'USD', 'NGN')
                - email: Customer email
                - metadata: Additional metadata
                - payment_method: The payment method to use
                - callback_url: URL to redirect after payment
                - description: Payment description
            
        Returns:
            dict: The payment details including authorization URL and reference
        """
        amount = data.get('amount')
        currency = data.get('currency', 'NGN')
        email = data.get('email')
        metadata = data.get('metadata', {})
        payment_method = data.get('payment_method', 'credit_card')
        callback_url = data.get('callback_url')
        description = data.get('description', 'Payment for goods/services')
        
        if payment_method not in self.PAYMENT_METHODS:
            return {
                'status': 'error',
                'message': f"Invalid payment method: {payment_method}. Available methods: {', '.join(self.PAYMENT_METHODS)}"
            }
            
        # Generate unique transaction reference
        reference = data.get('reference', Transaction.generate_reference())
        
        # Create transaction record
        try:
            with db_transaction.atomic():
                tx = Transaction(
                    reference=reference,
                    amount=Decimal(str(amount)),
                    currency=currency.upper(),
                    payment_method=payment_method,
                    status='pending',
                    email=email,
                    description=description,
                    merchant=self.merchant
                )
                
                # Link customer if provided or find by email
                customer = data.get('customer')
                if customer:
                    tx.customer = customer
                elif email:
                    # Try to find or create customer by email
                    customer, created = Customer.objects.get_or_create(
                        email=email,
                        defaults={'name': email.split('@')[0]}
                    )
                    tx.customer = customer
                
                # Set metadata
                if metadata:
                    tx.set_metadata(metadata)
                    
                tx.save()
                
                # Generate checkout URL
                checkout_url = f"/payments/checkout/{reference}/"
                
                return {
                    'status': 'success',
                    'message': "Payment initialized",
                    'data': {
                        'reference': reference,
                        'authorization_url': checkout_url,
                        'access_code': hashlib.md5(reference.encode()).hexdigest()[:8],
                        'amount': float(amount),
                        'currency': currency
                    }
                }
                
        except Exception as e:
            logger.error(f"Failed to initialize payment: {str(e)}")
            return {
                'status': 'error',
                'message': f"Failed to initialize payment: {str(e)}"
            }
    
    def _validate_payment_details(self, payment_method, payment_details):
        """Validate payment details based on method"""
        if payment_method in ['credit_card', 'debit_card']:
            # Validate card details
            card = payment_details.get('card', {})
            if not card:
                return {'success': False, 'error': 'Card details required'}
                
            # Check required fields
            required_fields = ['number', 'expiry_month', 'expiry_year', 'cvv']
            for field in required_fields:
                if field not in card:
                    return {'success': False, 'error': f'Missing card field: {field}'}
            
            # Basic card validation
            card_number = card['number'].replace(' ', '')
            if not card_number.isdigit():
                return {'success': False, 'error': 'Card number must contain only digits'}
                
            if len(card_number) < 13 or len(card_number) > 19:
                return {'success': False, 'error': 'Invalid card number length'}
            
            # Luhn algorithm check (basic card validation)
            if not self._validate_card_number(card_number):
                return {'success': False, 'error': 'Invalid card number'}
                
            # Expiry date validation
            try:
                exp_month = int(card['expiry_month'])
                exp_year = int(card['expiry_year'])
                
                if exp_month < 1 or exp_month > 12:
                    return {'success': False, 'error': 'Invalid expiry month'}
                    
                now = datetime.datetime.now()
                if len(str(exp_year)) == 2:
                    exp_year += 2000  # Convert 2-digit year to 4-digit
                    
                if exp_year < now.year or (exp_year == now.year and exp_month < now.month):
                    return {'success': False, 'error': 'Card has expired'}
            except ValueError:
                return {'success': False, 'error': 'Invalid expiry date format'}
                
            # CVV validation - simple length check
            cvv = str(card['cvv'])
            if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                return {'success': False, 'error': 'Invalid CVV'}
                
            return {'success': True}
            
        elif payment_method == 'bank_transfer':
            # Validate bank details
            bank = payment_details.get('bank', {})
            if not bank:
                return {'success': False, 'error': 'Bank details required'}
                
            required_fields = ['account_number', 'bank_code']
            for field in required_fields:
                if field not in bank:
                    return {'success': False, 'error': f'Missing bank field: {field}'}
                    
            return {'success': True}
            
        elif payment_method == 'mobile_money':
            # Validate mobile money details
            mobile = payment_details.get('mobile', {})
            if not mobile:
                return {'success': False, 'error': 'Mobile money details required'}
                
            required_fields = ['phone_number', 'provider']
            for field in required_fields:
                if field not in mobile:
                    return {'success': False, 'error': f'Missing mobile money field: {field}'}
                    
            return {'success': True}
            
        # For simpler methods like USSD and QR code, minimal validation
        return {'success': True}
    
    def _validate_card_number(self, card_number):
        """Implement Luhn algorithm for card number validation"""
        # Remove any spaces or dashes
        card_number = card_number.replace(' ', '').replace('-', '')
        
        if not card_number.isdigit():
            return False
            
        # Convert to list of digits
        digits = [int(d) for d in card_number]
        
        # Double every second digit from the right
        for i in range(len(digits) - 2, -1, -2):
            digits[i] *= 2
            if digits[i] > 9:
                digits[i] -= 9
                
        # Sum all digits
        total = sum(digits)
        
        # If total is divisible by 10, the number is valid
        return total % 10 == 0
    
    def _detect_card_type(self, card_number):
        """Detect card type based on BIN (first 6 digits)"""
        # Use TokenizationService to detect card type
        return TokenizationService.get_card_type(card_number)
    
    def _process_card_payment(self, transaction, payment_details):
        """
        Process a card payment
        
        In a real system, this would integrate with a secure payment processing system.
        For this standalone version, we'll simulate payment processing.
        """
        card = payment_details.get('card', {})
        
        # Get BIN (first 6) and last4 of the card
        card_number = card.get('number', '').replace(' ', '')
        bin_number = card_number[:6] if len(card_number) >= 6 else ''
        last4 = card_number[-4:] if len(card_number) >= 4 else ''
        
        # Detect card type
        card_type = self._detect_card_type(card_number)
        
        # Tokenize card data for recurring use
        try:
            token, masked_details = TokenizationService.tokenize_card(card)
            fingerprint = TokenizationService.generate_fingerprint(card_number)
            
            # Store token reference for future use
            if transaction.customer:
                # Check if token already exists for this card
                existing_methods = PaymentMethod.objects.filter(
                    customer=transaction.customer,
                    method_type='card',
                    fingerprint=fingerprint
                )
                
                if not existing_methods.exists():
                    # Save new payment method with token
                    method = PaymentMethod(
                        customer=transaction.customer,
                        provider='internal',
                        method_type='card',
                        card_type=card_type,
                        last4=last4,
                        exp_month=masked_details['expiry_month'],
                        exp_year=masked_details['expiry_year'],
                        reference=token,
                        fingerprint=fingerprint
                    )
                    
                    # If this is the customer's first payment method, make it default
                    if not PaymentMethod.objects.filter(customer=transaction.customer).exists():
                        method.is_default = True
                        
                    method.save()
        except Exception as e:
            logger.warning(f"Failed to tokenize card: {str(e)}")
            # continue with payment processing even if tokenization fails
        
        # Simulate processing (in real world, call to payment processor happens here)
        # For demo purposes, we'll use a random success rate
        # In production, this is where you'd implement actual card processing logic
        success = random.random() < self.success_rate
        
        if success:
            # Store truncated card info in transaction metadata
            metadata = transaction.get_metadata() or {}
            payment_info = metadata.get('payment_info', {})
            payment_info.update({
                'card_type': card_type,
                'last4': last4,
                'exp_month': card.get('expiry_month'),
                'exp_year': card.get('expiry_year'),
                'bin': bin_number
            })
            metadata['payment_info'] = payment_info
            transaction.set_metadata(metadata)
            
            return {
                'success': True,
                'status': 'success',
                'transaction_id': transaction.reference,
                'message': 'Payment processed successfully',
                'card_type': card_type,
                'last4': last4
            }
        else:
            # Generate a realistic error message
            error_reasons = [
                'Insufficient funds',
                'Card declined by issuer',
                'Card reported as lost or stolen',
                'Invalid card details',
                'Transaction limit exceeded'
            ]
            error_message = random.choice(error_reasons)
            
            return {
                'success': False,
                'status': 'failed',
                'message': f'Payment failed: {error_message}',
                'error': error_message
            }
    
    def _process_bank_transfer(self, transaction, payment_details):
        """Process bank transfer payments"""
        # In a real system, this would provide bank account details for manual transfer
        # or integrate with local bank transfer APIs
        
        # Simulate successful creation of payment instructions
        bank = payment_details.get('bank', {})
        account_number = bank.get('account_number', '')
        bank_code = bank.get('bank_code', '')
        
        # Generate virtual account or payment reference (in a real system)
        payment_reference = f"TR{transaction.reference[-8:]}"
        
        # Store payment reference in transaction metadata
        metadata = transaction.get_metadata() or {}
        payment_info = metadata.get('payment_info', {})
        payment_info.update({
            'payment_reference': payment_reference,
            'bank_code': bank_code,
            'account_number_last4': account_number[-4:] if len(account_number) >= 4 else '',
            'payment_method': 'bank_transfer'
        })
        metadata['payment_info'] = payment_info
        transaction.set_metadata(metadata)
        
        # For demo, we'll return instructions
        # In a real system, bank transfers typically start as pending
        # and then get confirmed after the transfer is detected
        return {
            'success': True,
            'status': 'pending',
            'message': 'Bank transfer instructions created',
            'payment_reference': payment_reference,
            'instructions': {
                'bank_name': 'Demo Bank',
                'account_number': '1234567890',
                'account_name': 'HamsukyPay Merchant',
                'reference': payment_reference
            }
        }
    
    def _process_mobile_money(self, transaction, payment_details):
        """Process mobile money payments"""
        mobile = payment_details.get('mobile', {})
        phone_number = mobile.get('phone_number', '')
        provider = mobile.get('provider', '')
        
        # Store mobile money info in transaction metadata
        metadata = transaction.get_metadata() or {}
        payment_info = metadata.get('payment_info', {})
        payment_info.update({
            'phone_number': phone_number[-4:],  # Store only last 4 digits for privacy
            'provider': provider,
            'payment_method': 'mobile_money'
        })
        metadata['payment_info'] = payment_info
        transaction.set_metadata(metadata)
        
        # Simulate success rate
        success = random.random() < self.success_rate
        
        if success:
            return {
                'success': True,
                'status': 'success',
                'message': 'Mobile money payment successful',
                'transaction_id': transaction.reference
            }
        else:
            error_reasons = [
                'Insufficient balance',
                'Transaction timed out',
                'USSD session expired',
                'Customer canceled payment',
                'Mobile money service unavailable'
            ]
            error_message = random.choice(error_reasons)
            
            return {
                'success': False,
                'status': 'failed',
                'message': f'Payment failed: {error_message}',
                'error': error_message
            }
    
    def _process_ussd_payment(self, transaction, payment_details):
        """Process USSD payments"""
        # Generate USSD code for payment
        ussd_code = f"*999*{transaction.reference[-6:]}#"
        
        # Store USSD info in transaction metadata
        metadata = transaction.get_metadata() or {}
        payment_info = metadata.get('payment_info', {})
        payment_info.update({
            'ussd_code': ussd_code,
            'payment_method': 'ussd'
        })
        metadata['payment_info'] = payment_info
        transaction.set_metadata(metadata)
        
        # USSD is typically pending until confirmed
        return {
            'success': True,
            'status': 'pending',
            'message': 'USSD payment instructions created',
            'ussd_code': ussd_code,
            'instructions': 'Dial the USSD code on your mobile phone to complete payment'
        }
    
    def _process_qr_payment(self, transaction, payment_details):
        """Process QR code payments"""
        # In a real system, generate actual QR code
        # Here we'll just simulate the process
        qr_reference = f"QR-{transaction.reference[-8:]}"
        qr_image_url = f"/static/qr_codes/{qr_reference}.png"  # This would be a real QR code in production
        
        # Store QR info in transaction metadata
        metadata = transaction.get_metadata() or {}
        payment_info = metadata.get('payment_info', {})
        payment_info.update({
            'qr_reference': qr_reference,
            'payment_method': 'qr_code'
        })
        metadata['payment_info'] = payment_info
        transaction.set_metadata(metadata)
        
        # QR is typically pending until scanned
        return {
            'success': True,
            'status': 'pending',
            'message': 'QR code payment created',
            'qr_reference': qr_reference,
            'qr_image_url': qr_image_url,
            'instructions': 'Scan the QR code with your banking app to complete payment'
        }
    
    def _save_customer_payment_method(self, customer, payment_details):
        """Save customer payment method for future transactions"""
        if not customer:
            return
            
        # For card payments
        if 'card' in payment_details:
            card = payment_details['card']
            card_number = card.get('number', '').replace(' ', '')
            
            # Use TokenizationService for secure storage
            try:
                token, masked_details = TokenizationService.tokenize_card(card)
                fingerprint = TokenizationService.generate_fingerprint(card_number)
                card_type = TokenizationService.get_card_type(card_number)
                
                # Check if this card is already saved
                existing_methods = PaymentMethod.objects.filter(
                    customer=customer,
                    method_type='card',
                    fingerprint=fingerprint
                )
                
                if not existing_methods.exists():
                    # Save new payment method
                    method = PaymentMethod(
                        customer=customer,
                        provider='internal',
                        method_type='card',
                        card_type=card_type,
                        last4=masked_details['last4'],
                        exp_month=masked_details['expiry_month'],
                        exp_year=masked_details['expiry_year'],
                        reference=token,
                        fingerprint=fingerprint
                    )
                    
                    # If this is the customer's first payment method, make it default
                    if not PaymentMethod.objects.filter(customer=customer).exists():
                        method.is_default = True
                        
                    method.save()
                    return method
                
                return existing_methods.first()
                
            except Exception as e:
                logger.error(f"Error saving payment method: {str(e)}")
                return None
                
        # For bank accounts
        elif 'bank' in payment_details:
            bank = payment_details['bank']
            account_number = bank.get('account_number', '')
            bank_code = bank.get('bank_code', '')
            bank_name = bank.get('bank_name', 'Bank')
            
            if len(account_number) >= 4:
                last4 = account_number[-4:]
                
                # Check if this bank account is already saved
                existing_methods = PaymentMethod.objects.filter(
                    customer=customer,
                    method_type='bank_account',
                    last4=last4,
                    bank_name=bank_name
                )
                
                if not existing_methods.exists():
                    # Save new payment method
                    method = PaymentMethod(
                        customer=customer,
                        provider='internal',
                        method_type='bank_account',
                        last4=last4,
                        bank_name=bank_name,
                        account_name=bank.get('account_name', ''),
                        reference=f"pm_{uuid.uuid4().hex[:16]}"
                    )
                    
                    # If this is the customer's first payment method, make it default
                    if not PaymentMethod.objects.filter(customer=customer).exists():
                        method.is_default = True
                        
                    method.save()
                    return method
        
        return None
    
    def process_payment(self, reference, payment_details):
        """
        Process a payment transaction
        
        Args:
            reference: Transaction reference
            payment_details: Dict with payment details (card details, etc.)
            
        Returns:
            dict: The payment result
        """
        # Find transaction
        try:
            tx = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return {
                'status': 'error',
                'message': f"Transaction with reference {reference} not found"
            }
        
        # Check transaction status
        if tx.status != 'pending':
            return {
                'status': 'error',
                'message': f"Transaction is already in {tx.status} state"
            }
        
        # Validate payment details
        payment_method = tx.payment_method
        validation_result = self._validate_payment_details(payment_method, payment_details)
        if not validation_result['success']:
            tx.status = 'failed'
            # Store error in metadata
            metadata = tx.get_metadata() or {}
            metadata['error'] = validation_result['error']
            tx.set_metadata(metadata)
            tx.save()
            
            return {
                'status': 'error',
                'message': validation_result['error']
            }
            
        # Process payment based on method
        try:
            if payment_method in ['credit_card', 'debit_card']:
                result = self._process_card_payment(tx, payment_details)
            elif payment_method == 'bank_transfer':
                result = self._process_bank_transfer(tx, payment_details)
            elif payment_method == 'mobile_money':
                result = self._process_mobile_money(tx, payment_details)
            elif payment_method == 'ussd':
                result = self._process_ussd_payment(tx, payment_details)
            elif payment_method == 'qr_code':
                result = self._process_qr_payment(tx, payment_details)
            else:
                result = {
                    'success': False,
                    'status': 'error',
                    'message': f"Unsupported payment method: {payment_method}"
                }
                
            # Update transaction based on result
            if result['success']:
                tx.status = result.get('status', 'success')
                
                # If the payment was actually successful (not just pending)
                if tx.status == 'success':
                    tx.completed_at = timezone.now()
                    
                    # Save payment method for customer if available
                    if tx.customer and payment_method in ['credit_card', 'debit_card', 'bank_transfer']:
                        self._save_customer_payment_method(tx.customer, payment_details)
            else:
                tx.status = 'failed'
                # Store error in metadata
                metadata = tx.get_metadata() or {}
                metadata['error'] = result.get('message', 'Unknown error')
                tx.set_metadata(metadata)
                tx.completed_at = timezone.now()
            
            tx.save()
            
            # Add transaction reference to result
            if 'reference' not in result:
                result['reference'] = reference
                
            return result
            
        except Exception as e:
            logger.error(f"Failed to process payment: {str(e)}")
            tx.status = 'failed'
            # Store error in metadata
            metadata = tx.get_metadata() or {}
            metadata['error'] = str(e)
            tx.set_metadata(metadata)
            tx.completed_at = timezone.now()
            tx.save()
            
            return {
                'status': 'error',
                'message': f"Failed to process payment: {str(e)}",
                'reference': reference
            }
    
    def verify_payment(self, reference):
        """
        Verify a payment transaction
        
        Args:
            reference: Transaction reference
            
        Returns:
            dict: The verification result
        """
        try:
            tx = Transaction.objects.get(reference=reference)
            
            # Build response
            result = {
                'status': 'success',
                'message': f"Transaction verified. Status: {tx.status}",
                'data': {
                    'reference': reference,
                    'status': tx.status,
                    'amount': float(tx.amount),
                    'currency': tx.currency,
                    'payment_method': tx.payment_method,
                    'customer_email': tx.email,
                    'created_at': tx.created_at.isoformat(),
                }
            }
            
            # Add completed date if available
            if tx.status in ['success', 'failed'] and hasattr(tx, 'completed_at') and tx.completed_at:
                result['data']['completed_at'] = tx.completed_at.isoformat()
                
            # Add metadata if available
            metadata = tx.get_metadata()
            if metadata:
                # Don't include sensitive data
                if 'payment_info' in metadata:
                    payment_info = metadata['payment_info']
                    safe_payment_info = {k: v for k, v in payment_info.items() 
                                        if k not in ['card_number', 'cvv', 'pin']}
                    metadata['payment_info'] = safe_payment_info
                    
                result['data']['metadata'] = metadata
                
            return result
            
        except Transaction.DoesNotExist:
            return {
                'status': 'error',
                'message': f"Transaction with reference {reference} not found"
            }
    
    def process_refund(self, reference, amount=None, reason=None):
        """
        Process a refund for a transaction
        
        Args:
            reference: Transaction reference
            amount: Amount to refund (if partial refund)
            reason: Reason for refund
            
        Returns:
            dict: The refund result
        """
        try:
            tx = Transaction.objects.get(reference=reference)
            
            # Check if transaction can be refunded
            if tx.status != 'success':
                return {
                    'status': 'error',
                    'message': f"Cannot refund transaction with status {tx.status}"
                }
                
            # Get refund amount
            refund_amount = Decimal(str(amount)) if amount else tx.amount
            
            # Validate refund amount
            if refund_amount <= 0 or refund_amount > tx.amount:
                return {
                    'status': 'error',
                    'message': f"Invalid refund amount: {refund_amount}"
                }
                
            # Create refund record
            refund_reference = f"RF-{Transaction.generate_reference()}"
            
            # In a real system, process the actual refund
            # For this demo, we'll simulate a successful refund
            
            # Update original transaction metadata
            metadata = tx.get_metadata() or {}
            refunds = metadata.get('refunds', [])
            refunds.append({
                'reference': refund_reference,
                'amount': float(refund_amount),
                'reason': reason,
                'date': timezone.now().isoformat()
            })
            metadata['refunds'] = refunds
            tx.set_metadata(metadata)
            tx.save()
            
            return {
                'status': 'success',
                'message': 'Refund processed successfully',
                'data': {
                    'refund_reference': refund_reference,
                    'original_reference': reference,
                    'amount': float(refund_amount),
                    'currency': tx.currency,
                    'date': timezone.now().isoformat()
                }
            }
            
        except Transaction.DoesNotExist:
            return {
                'status': 'error',
                'message': f"Transaction with reference {reference} not found"
            }
    
    def process_subscription_payment(self, subscription):
        """Process a subscription payment and create a transaction record."""
        customer = subscription.customer
        plan = subscription.plan
        
        # Create transaction record
        reference = Transaction.generate_reference()
        transaction = Transaction.objects.create(
            reference=reference,
            amount=plan.amount,
            currency=plan.currency,
            customer=customer,
            email=customer.email,
            status='pending',
            description=f"Subscription payment for {plan.name}",
            merchant=plan.merchant,
            payment_method="subscription_auto_charge"
        )
        
        # Set transaction metadata
        metadata = {
            "subscription_reference": subscription.reference,
            "plan_name": plan.name,
            "plan_id": str(plan.id),
            "is_subscription": True
        }
        transaction.set_metadata(metadata)
        transaction.save()
        
        # Get customer's saved payment method
        try:
            payment_method = PaymentMethod.objects.filter(
                customer=customer, 
                is_default=True
            ).first()
            
            if not payment_method:
                # No default payment method, try to get any payment method
                payment_method = PaymentMethod.objects.filter(customer=customer).first()
                
            if payment_method:
                # Simulate processing with the saved payment method
                # In a real system, this would use the saved payment details to process the payment
                
                # Check if payment method is tokenized
                if payment_method.method_type == 'card' and payment_method.reference:
                    if payment_method.reference.startswith('tok_'):
                        # Use the tokenized card data
                        try:
                            # In a real system, we would use the token to process the payment
                            # Here we'll just simulate success or failure
                            success = random.random() < self.success_rate
                            
                            if success:
                                transaction.status = "success"
                                transaction.completed_at = timezone.now()
                                transaction.save()
                                
                                return True, transaction
                            else:
                                transaction.status = "failed"
                                transaction.completed_at = timezone.now()
                                transaction.save()
                                
                                return False, transaction
                        except Exception as e:
                            logger.error(f"Failed to process subscription with token: {str(e)}")
                
                # Fallback to regular simulation if token processing fails
                success = random.random() < self.success_rate
                
                if success:
                    transaction.status = "success"
                    transaction.completed_at = timezone.now()
                    transaction.save()
                    
                    return True, transaction
                else:
                    transaction.status = "failed"
                    transaction.completed_at = timezone.now()
                    transaction.save()
                    
                    return False, transaction
            else:
                # No payment method available
                transaction.status = "failed"
                metadata = transaction.get_metadata() or {}
                metadata["error"] = "No payment method available for subscription"
                transaction.set_metadata(metadata)
                transaction.completed_at = timezone.now()
                transaction.save()
                
                return False, transaction
                
        except Exception as e:
            logger.error(f"Error processing subscription payment: {str(e)}")
            transaction.status = "failed"
            metadata = transaction.get_metadata() or {}
            metadata["error"] = str(e)
            transaction.set_metadata(metadata)
            transaction.completed_at = timezone.now()
            transaction.save()
            
            return False, transaction


def get_payment_processor(merchant=None):
    """Factory function to get the standalone payment processor."""
    return StandalonePaymentProcessor(merchant)


def process_subscription_payment(subscription):
    """Process a subscription payment and create a transaction record."""
    processor = get_payment_processor(subscription.plan.merchant)
    return processor.process_subscription_payment(subscription)