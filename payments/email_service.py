"""
Email notification service for HamsukyPay
"""
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
import logging

logger = logging.getLogger(__name__)

class EmailService:
    """
    Service class for handling email notifications
    """
    
    @staticmethod
    def send_email(subject, to_email, template_name, context, from_email=None):
        """
        Send an email using a template
        
        Args:
            subject: Email subject
            to_email: Recipient email address
            template_name: Template name (without .html extension)
            context: Context dictionary for template rendering
            from_email: From email address (default: settings.DEFAULT_FROM_EMAIL)
        """
        try:
            # Use default from email if not provided
            if from_email is None:
                from_email = settings.DEFAULT_FROM_EMAIL
                
            # Render HTML content from template
            html_content = render_to_string(f'payments/emails/{template_name}.html', context)
            
            # Create plain text version
            text_content = strip_tags(html_content)
            
            # Create message
            msg = EmailMultiAlternatives(subject, text_content, from_email, [to_email])
            msg.attach_alternative(html_content, "text/html")
            
            # Send email
            msg.send()
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False
    
    @classmethod
    def send_merchant_welcome_email(cls, merchant):
        """
        Send welcome email to new merchant
        
        Args:
            merchant: Merchant object
        """
        subject = "Welcome to HamsukyPay!"
        to_email = merchant.business_email
        template_name = "merchant_welcome"
        
        context = {
            'merchant': merchant,
            'first_name': merchant.user.first_name,
        }
        
        return cls.send_email(subject, to_email, template_name, context)
    
    @classmethod
    def send_password_changed_notification(cls, merchant):
        """
        Send notification when merchant changes their password
        
        Args:
            merchant: Merchant object
        """
        subject = "Password Changed - HamsukyPay"
        to_email = merchant.business_email
        template_name = "password_changed"
        
        context = {
            'first_name': merchant.user.first_name,
        }
        
        return cls.send_email(subject, to_email, template_name, context)
    
    @classmethod
    def send_transaction_success_notification(cls, transaction):
        """
        Send notification when transaction is successful
        
        Args:
            transaction: Transaction object
        """
        subject = f"Payment Successful - {transaction.reference}"
        to_email = transaction.email
        template_name = "transaction_success"

        merchant_name = transaction.merchant.business_name if transaction.merchant else "HamsukyPay"
        merchant_email = transaction.merchant.business_email if transaction.merchant else settings.DEFAULT_FROM_EMAIL
        
        context = {
            'transaction': transaction,
            'merchant_name': merchant_name,
            'support_email': merchant_email,
            'amount': float(transaction.amount),
            'currency': transaction.currency,
        }
        
        return cls.send_email(subject, to_email, template_name, context)
    
    @classmethod
    def send_transaction_failed_notification(cls, transaction):
        """
        Send notification when transaction fails
        
        Args:
            transaction: Transaction object
        """
        subject = f"Payment Failed - {transaction.reference}"
        to_email = transaction.email
        template_name = "transaction_failed"

        merchant_name = transaction.merchant.business_name if transaction.merchant else "HamsukyPay"
        merchant_email = transaction.merchant.business_email if transaction.merchant else settings.DEFAULT_FROM_EMAIL
        
        # Get error message from metadata if available
        metadata = transaction.get_metadata() or {}
        error_message = metadata.get('error_message', 'Unknown error')
        
        context = {
            'transaction': transaction,
            'merchant_name': merchant_name,
            'support_email': merchant_email,
            'amount': float(transaction.amount),
            'currency': transaction.currency,
            'error_message': error_message,
        }
        
        return cls.send_email(subject, to_email, template_name, context)
    
    @classmethod
    def send_subscription_confirmation(cls, subscription):
        """
        Send confirmation when subscription is created
        
        Args:
            subscription: Subscription object
        """
        subject = f"Subscription Confirmed - {subscription.plan.name}"
        to_email = subscription.customer.email
        template_name = "subscription_confirmation"

        merchant_name = subscription.plan.merchant.business_name if subscription.plan.merchant else "HamsukyPay"
        
        context = {
            'customer': subscription.customer,
            'subscription': subscription,
            'merchant_name': merchant_name,
        }
        
        return cls.send_email(subject, to_email, template_name, context)

    @staticmethod
    def send_verification_approved_email(merchant):
        """Sends a verification approval email to the merchant"""
        subject = "Your HamsukyPay merchant account has been verified!"
        
        message = f"""
        Dear {merchant.business_name},
        
        Congratulations! Your HamsukyPay merchant account has been verified and approved.
        
        You now have full access to all HamsukyPay features and payment processing capabilities.
        
        Your account details:
        - Business Name: {merchant.business_name}
        - Business Email: {merchant.business_email}
        - Public Key: {merchant.public_key}
        
        If you have any questions, please don't hesitate to contact our support team.
        
        Best regards,
        The HamsukyPay Team
        """
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[merchant.business_email]
        )
        
        return True

    @staticmethod
    def send_verification_rejected_email(merchant, reason=None):
        """Sends a verification rejection email to the merchant"""
        subject = "Update on your HamsukyPay merchant account verification"
        
        if not reason:
            reason = "We were unable to verify your business information with the details provided."
        
        message = f"""
        Dear {merchant.business_name},
        
        We regret to inform you that your merchant account verification was not approved.
        
        Reason: {reason}
        
        You can update your business information and request verification again by logging into your account.
        
        If you have any questions or need assistance, please contact our support team.
        
        Best regards,
        The HamsukyPay Team
        """
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[merchant.business_email]
        )
        
        return True

    @staticmethod
    def send_custom_email(email_address, subject, message):
        """Sends a custom email to the specified recipient"""
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email_address]
        )
        
        return True