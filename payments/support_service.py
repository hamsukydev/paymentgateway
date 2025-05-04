import logging
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.contrib.auth.models import User
from django.urls import reverse
from .models import SupportTicket, SupportTicketReply, SupportTicketNotification

logger = logging.getLogger(__name__)

class SupportNotificationService:
    """
    Service for handling support ticket notifications, including:
    - Email notifications for admins and merchants
    - System notifications for the admin interface
    - Notifications when tickets are created, updated, or replied to
    """
    
    @staticmethod
    def notify_new_ticket(ticket):
        """
        Send notifications about a new support ticket.
        
        Args:
            ticket: The SupportTicket instance that was created
        """
        try:
            # System notification for admins
            SupportNotificationService._create_system_notification(
                ticket=ticket,
                notification_type='new_ticket'
            )
            
            # Email to admins
            SupportNotificationService._send_admin_notification_email(
                ticket=ticket,
                subject=f"[HamsukyPay] New {ticket.get_priority_display()} Support Ticket: {ticket.ticket_id}",
                template='admin_new_ticket.html'
            )
            
            # Email confirmation to merchant
            SupportNotificationService._send_merchant_notification_email(
                ticket=ticket,
                subject=f"[HamsukyPay] Your Support Ticket #{ticket.ticket_id} has been received",
                template='merchant_ticket_received.html'
            )
            
            logger.info(f"Successfully sent notifications for new ticket {ticket.ticket_id}")
            
        except Exception as e:
            logger.error(f"Error sending new ticket notifications: {str(e)}")
            raise
    
    @staticmethod
    def notify_ticket_reply(reply):
        """
        Send notifications about a new reply to a support ticket.
        
        Args:
            reply: The SupportTicketReply instance that was created
        """
        try:
            ticket = reply.ticket
            is_admin_reply = reply.is_admin
            
            if is_admin_reply:
                # Admin replied, notify merchant
                SupportNotificationService._send_merchant_notification_email(
                    ticket=ticket,
                    subject=f"[HamsukyPay] New response to your Support Ticket #{ticket.ticket_id}",
                    template='merchant_ticket_reply.html',
                    context_extras={'reply': reply}
                )
            else:
                # Merchant replied, notify admins
                SupportNotificationService._create_system_notification(
                    ticket=ticket,
                    notification_type='ticket_reply'
                )
                
                SupportNotificationService._send_admin_notification_email(
                    ticket=ticket,
                    subject=f"[HamsukyPay] New merchant reply on Ticket: {ticket.ticket_id}",
                    template='admin_ticket_reply.html',
                    context_extras={'reply': reply}
                )
            
            logger.info(f"Successfully sent notifications for reply on ticket {ticket.ticket_id}")
            
        except Exception as e:
            logger.error(f"Error sending reply notifications: {str(e)}")
            raise
    
    @staticmethod
    def notify_status_change(ticket, previous_status):
        """
        Send notifications about a ticket status change.
        
        Args:
            ticket: The SupportTicket instance that was updated
            previous_status: The previous status before the change
        """
        try:
            # Only notify the merchant if the status was changed by admin
            # and it's a significant status change
            significant_changes = [
                ('in_progress', 'resolved'),
                ('open', 'resolved'),
                ('resolved', 'closed'),
                ('in_progress', 'closed'),
            ]
            
            # Convert to display names for better readability
            status_display = dict(SupportTicket.TICKET_STATUS_CHOICES)
            previous_status_display = status_display.get(previous_status, previous_status)
            
            # Notify merchant about significant status changes
            if (previous_status, ticket.status) in significant_changes:
                SupportNotificationService._send_merchant_notification_email(
                    ticket=ticket,
                    subject=f"[HamsukyPay] Your Support Ticket #{ticket.ticket_id} status has been updated",
                    template='merchant_status_update.html',
                    context_extras={
                        'previous_status_display': previous_status_display
                    }
                )
            
            # System notification for admins about the change
            SupportNotificationService._create_system_notification(
                ticket=ticket,
                notification_type='status_change'
            )
            
            logger.info(f"Successfully sent notifications for status change on ticket {ticket.ticket_id}")
            
        except Exception as e:
            logger.error(f"Error sending status change notifications: {str(e)}")
            raise
    
    @staticmethod
    def _create_system_notification(ticket, notification_type):
        """
        Create a system notification for admin dashboard.
        
        Args:
            ticket: The associated SupportTicket instance
            notification_type: Type of notification (new_ticket, ticket_reply, status_change, etc.)
        """
        # Create notification entry
        notification = SupportTicketNotification.objects.create(
            ticket=ticket,
            notification_type=notification_type
        )
        
        # If ticket is assigned to someone, mark this notification for them
        if ticket.assigned_to:
            notification.recipient = ticket.assigned_to
            notification.save()
        
        return notification
    
    @staticmethod
    def _send_admin_notification_email(ticket, subject, template, context_extras=None):
        """
        Send email notification to admin staff.
        
        Args:
            ticket: The SupportTicket instance
            subject: Email subject
            template: Template name (without the path prefix)
            context_extras: Optional additional context for the template
        """
        # Get admin emails - either from ticket assigned_to or all admins
        if ticket.assigned_to:
            recipient_list = [ticket.assigned_to.email]
        else:
            # Get all staff users with admin access to support
            recipient_list = list(User.objects.filter(
                is_staff=True, 
                is_active=True
            ).values_list('email', flat=True))
        
        # Filter out empty emails
        recipient_list = [email for email in recipient_list if email]
        
        if not recipient_list:
            logger.warning("No admin recipients found for support notification")
            return
        
        # Build the admin URL for the ticket
        admin_url = f"{settings.BASE_URL}{reverse('admin_support_ticket_detail', args=[ticket.id])}"
        
        # Prepare context for the email template
        context = {
            'ticket': ticket,
            'ticket_url': admin_url,
            'merchant': ticket.merchant,
            'is_admin': True,
        }
        
        # Add any extra context variables
        if context_extras:
            context.update(context_extras)
        
        # Send the email
        try:
            SupportNotificationService._send_email(
                subject=subject,
                recipient_list=recipient_list,
                template_name=f'emails/support/{template}',
                context=context
            )
            
            # Record successful notification
            for email in recipient_list:
                SupportTicketNotification.objects.create(
                    ticket=ticket,
                    notification_type='new_ticket',
                    recipient_email=email,
                    delivered=True,
                    template_used=template
                )
                
        except Exception as e:
            logger.error(f"Failed to send support ticket notification: {str(e)}")
            # Record failed notification attempt
            SupportTicketNotification.objects.create(
                ticket=ticket,
                notification_type='new_ticket',
                recipient_email=','.join(recipient_list),
                delivered=False,
                error_message=str(e),
                template_used=template
            )
    
    @staticmethod
    def _send_merchant_notification_email(ticket, subject, template, context_extras=None):
        """
        Send email notification to merchant.
        
        Args:
            ticket: The SupportTicket instance
            subject: Email subject
            template: Template name (without the path prefix)
            context_extras: Optional additional context for the template
        """
        # Get merchant email
        merchant = ticket.merchant
        if not merchant.user.email:
            logger.warning(f"Merchant {merchant.id} has no email address for notifications")
            return
        
        # Build the merchant URL for the ticket
        merchant_url = f"{settings.BASE_URL}{reverse('merchant_support_detail', args=[ticket.id])}"
        
        # Prepare context for the email template
        context = {
            'ticket': ticket,
            'ticket_url': merchant_url,
            'merchant': merchant,
            'is_admin': False,
        }
        
        # Add any extra context variables
        if context_extras:
            context.update(context_extras)
        
        # Send the email
        try:
            SupportNotificationService._send_email(
                subject=subject,
                recipient_list=[merchant.user.email],
                template_name=f'emails/support/{template}',
                context=context
            )
            
            # Record successful notification
            SupportTicketNotification.objects.create(
                ticket=ticket,
                notification_type='new_ticket',
                recipient_email=merchant.user.email,
                delivered=True,
                template_used=template
            )
                
        except Exception as e:
            logger.error(f"Failed to send support ticket notification: {str(e)}")
            # Record failed notification attempt
            SupportTicketNotification.objects.create(
                ticket=ticket,
                notification_type='new_ticket',
                recipient_email=merchant.user.email,
                delivered=False,
                error_message=str(e),
                template_used=template
            )
    
    @staticmethod
    def _send_email(subject, recipient_list, template_name, context):
        """
        Helper method to send an HTML email from a template.
        
        Args:
            subject: Email subject
            recipient_list: List of email addresses to send to
            template_name: Template path
            context: Context for the template
        """
        html_content = render_to_string(template_name, context)
        text_content = strip_tags(html_content)
        
        msg = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()