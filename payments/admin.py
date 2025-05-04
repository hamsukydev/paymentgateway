from django.contrib import admin
from .models import Customer, Transaction, PaymentPlan, Subscription, SupportTicket, SupportTicketReply, SupportTicketNotification

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'created_at')
    search_fields = ('email', 'name')
    list_filter = ('created_at',)
    ordering = ('-created_at',)

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('reference', 'email', 'amount', 'currency', 'status', 'created_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('reference', 'email')
    readonly_fields = ('reference', 'created_at', 'updated_at')
    ordering = ('-created_at',)

@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'currency', 'interval', 'active')
    list_filter = ('interval', 'active', 'created_at')
    search_fields = ('name', 'description')

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'plan', 'status', 'next_payment_date', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('reference', 'customer__email')
    raw_id_fields = ('customer', 'plan')

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ('ticket_id', 'subject', 'merchant', 'status', 'priority', 'created_at')
    list_filter = ('status', 'priority', 'created_at')
    search_fields = ('ticket_id', 'subject', 'merchant__business_name')
    ordering = ('-created_at',)

@admin.register(SupportTicketReply)
class SupportTicketReplyAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'user', 'is_admin', 'created_at')
    list_filter = ('is_admin', 'created_at')
    search_fields = ('message', 'ticket__ticket_id')
    ordering = ('-created_at',)

@admin.register(SupportTicketNotification)
class SupportTicketNotificationAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'notification_type', 'recipient', 'sent_at', 'delivered')
    list_filter = ('notification_type', 'delivered', 'sent_at')
    search_fields = ('ticket__ticket_id', 'recipient__username', 'recipient_email')
    ordering = ('-sent_at',)
