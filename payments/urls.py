from django.urls import path, include
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import DefaultRouter
from . import views
from .views import handle_webhook, HamsukyPayAPI
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required

# Define the app namespace
app_name = 'payments'

# Create a router for viewsets
router = DefaultRouter()
router.register(r'customers', views.CustomerViewSet)
router.register(r'transactions', views.TransactionViewSet)
router.register(r'plans', views.PaymentPlanViewSet)
router.register(r'subscriptions', views.SubscriptionViewSet)
router.register(r'merchants', views.MerchantViewSet)

# Standalone HamsukyPay API endpoints
standalone_api_urls = [
    # ... existing standalone API URLs ...
]

# Admin Custom URLs
admin_custom_urls = [
    path('dashboard/', views.custom_admin_dashboard, name='custom_admin_dashboard'),
    path('transactions/', views.admin_transactions, name='admin_transactions'),
    path('transactions/<str:reference>/', views.admin_transaction_detail, name='admin_transaction_detail'),
    path('merchants/', views.admin_merchants, name='admin_merchants'),
    path('merchants/<int:id>/', views.admin_merchant_detail, name='admin_merchant_detail'),
    path('customers/', views.admin_customers, name='admin_customers'),
    path('customers/<int:id>/', views.admin_customer_detail, name='admin_customer_detail'),
    path('subscriptions/', views.admin_subscriptions, name='admin_subscriptions'),
    path('plans/', views.admin_plans, name='admin_plans'),
    path('analytics/', views.admin_analytics, name='admin_analytics'),
    path('settings/', views.admin_settings, name='admin_settings'),
    path('compliance/', views.admin_compliance, name='admin_compliance'),
    path('users/', views.admin_user_management, name='admin_users'),
    path('documentation/', views.admin_documentation, name='admin_documentation'),
    path('support/', views.admin_support_tickets, name='admin_support_tickets'),
    path('support/<str:ticket_id>/', views.admin_support_ticket_detail, name='admin_support_ticket_detail'),
    path('login/', views.admin_login, name='admin_login'),
    path('logout/', views.admin_logout, name='admin_logout'),
]

# URL patterns
urlpatterns = [
    # Home page
    path('', views.home_view, name='home'),
    
    # New pages for pricing and solutions
    path('pricing/', views.pricing_view, name='pricing'),
    path('solutions/', views.solutions_view, name='solutions'),
    path('currency-converter/', views.currency_converter, name='currency_converter'), # Added currency converter URL
    
    # Developer pages
    path('integration-overview/', views.integration_overview, name='integration_overview'),
    
    # Note: Password reset URLs are now defined in the main urls.py file
    
    # API endpoints
    path('api/', include(router.urls)),
    path('api/payment/initialize/', views.InitiatePaymentView.as_view(), name='initialize-payment'),
    path('api/payment/verify/', views.VerifyPaymentView.as_view(), name='verify-payment'),
    path('api/payment/mobile-money/', views.MobileMoneyPaymentView.as_view(), name='mobile-money-payment'),
    path('api/payment/verify-mobile/', views.VerifyMobilePaymentView.as_view(), name='verify-mobile-payment'),
    path('api/payment/verify-qr/', views.VerifyQRPaymentView.as_view(), name='verify-qr-payment'),
    path('api/payment/verify-transfer/', views.VerifyTransferPaymentView.as_view(), name='verify-transfer-payment'),
    path('api/payment/verify-ussd/', views.VerifyUSSDPaymentView.as_view(), name='verify-ussd-payment'),
    path('api/subscription/create/', views.CreateSubscriptionView.as_view(), name='create-subscription'),
    path('api/merchant/register/', views.MerchantRegistrationView.as_view(), name='merchant-registration'),
    path('api/analytics/', views.AnalyticsAPIView.as_view(), name='analytics-api'),
    
    # Admin dashboard data API
    path('api/admin/dashboard-data/', views.admin_dashboard_data, name='admin-dashboard-data'),
    
    # Merchant web UI
    path('merchant/login/', views.merchant_login, name='merchant_login'),  # Added merchant login URL
    path('merchant/register/', views.merchant_register, name='merchant_register'),
    path('merchant/dashboard/', views.merchant_dashboard, name='merchant_dashboard'),
    path('merchant/transactions/', views.merchant_transactions, name='merchant_transactions'),
    path('merchant/customers/', views.merchant_customers, name='merchant_customers'),
    path('merchant/payment-links/', views.merchant_payment_links, name='merchant_payment_links'),  # Added Payment Links URL
    path('merchant/invoices/', views.merchant_invoices, name='merchant_invoices'),  # Added Invoices URL
    path('merchant/payouts/', views.merchant_payouts, name='merchant_payouts'),  # Added Payouts URL
    path('merchant/settings/', views.merchant_settings, name='merchant_settings'),
    path('merchant/api-docs/', views.merchant_api_docs, name='merchant_api_docs'),
    path('merchant/analytics/', views.analytics_view, name='merchant_analytics'),
    path('merchant/webhooks/', views.merchant_webhooks, name='merchant_webhooks'),
    path('merchant/webhooks/test/<int:webhook_id>/', views.test_webhook, name='test_webhook'),
    path('merchant/webhooks/delete/<int:webhook_id>/', views.delete_webhook, name='delete_webhook'),
    path('merchant/webhooks/status/<int:webhook_id>/', views.update_webhook_status, name='update_webhook_status'),
    path('merchant/support/', views.merchant_support, name='merchant_support'),  # Added Support URL
    
    # Merchant profile management
    path('merchant/update-profile/', views.merchant_update_profile, name='merchant_update_profile'),
    path('merchant/update-password/', views.merchant_update_password, name='merchant_update_password'),
    path('merchant/api-keys/', views.merchant_api_keys, name='merchant_api_keys'),
    path('merchant/regenerate-keys/', views.merchant_regenerate_keys, name='merchant_regenerate_keys'),
    path('merchant/delete-account/', views.merchant_delete_account, name='merchant_delete_account'),
    
    # Webhook endpoints for payment providers
    path('webhook/paystack/', views.handle_webhook, {'provider': 'paystack'}, name='paystack-webhook'),
    path('webhook/flutterwave/', views.handle_webhook, {'provider': 'flutterwave'}, name='flutterwave-webhook'),
    path('webhooks/<str:provider>/', handle_webhook, name='handle_webhook'),
    
    # Web UI endpoints
    path('payment/checkout/<str:reference>/', views.payment_checkout, name='payment-checkout'),
    path('payment/success/<str:reference>/', views.payment_success, name='payment-success'),

    # HamsukyPay Direct API endpoints
    path('api/v1/payments/initialize/', HamsukyPayAPI.initialize_payment, name='api-initialize-payment'),
    path('api/v1/payments/verify/<str:reference>/', HamsukyPayAPI.verify_payment, name='api-verify-payment'),
    path('api/v1/payments/process/<str:reference>/', HamsukyPayAPI.process_payment, name='api-process-payment'),
    path('api/v1/payments/refund/<str:reference>/', HamsukyPayAPI.process_refund, name='api-process-refund'),
    path('api/v1/payments/plans/create/', HamsukyPayAPI.create_payment_plan, name='api-create-payment-plan'),
    path('api/v1/customers/create/', HamsukyPayAPI.create_customer, name='api-create-customer'),
    path('api/v1/payments/subscriptions/create/', HamsukyPayAPI.create_subscription, name='api-create-subscription'),
    path('api/v1/payments/tokenize/', HamsukyPayAPI.tokenize_card, name='api-tokenize-card'),
    path('api/v1/webhooks/receive/', HamsukyPayAPI.receive_webhook, name='api-receive-webhook'),

    # Custom Admin URLs
    path('admin-custom/dashboard/', views.custom_admin_dashboard, name='custom_admin_dashboard'),
    path('admin-custom/transactions/', views.admin_transactions, name='admin_transactions'),
    path('admin-custom/transactions/<str:reference>/', views.admin_transaction_detail, name='admin_transaction_detail'),
    path('admin-custom/transactions/export/', views.admin_transaction_export, name='admin_transaction_export'),
    path('admin-custom/merchants/', views.admin_merchants, name='admin_merchants'),
    path('admin-custom/merchants/<int:id>/', views.admin_merchant_detail, name='admin_merchant_detail'),
    path('admin-custom/customers/', views.admin_customers, name='admin_customers'),
    path('admin-custom/customers/<int:id>/', views.admin_customer_detail, name='admin_customer_detail'),
    path('admin-custom/subscriptions/', views.admin_subscriptions, name='admin_subscriptions'),
    path('admin-custom/plans/', views.admin_plans, name='admin_plans'),
    path('admin-custom/analytics/', views.admin_analytics, name='admin_analytics'),
    path('admin-custom/settings/', views.admin_settings, name='admin_settings'),
    path('admin-custom/compliance/', views.admin_compliance, name='admin_compliance'),
    path('admin-custom/users/', views.admin_user_management, name='admin_user_management'),
    path('admin-custom/documentation/', login_required(views.admin_documentation), name='admin_documentation'),
    
    # User management URLs
    path('admin-custom/users/add/', views.admin_add_user, name='admin_add_user'),
    path('admin-custom/users/edit/<int:user_id>/', views.admin_edit_user, name='admin_edit_user'),
    path('admin-custom/users/delete/<int:user_id>/', views.admin_delete_user, name='admin_delete_user'),
    path('admin-custom/users/toggle-status/<int:user_id>/', views.admin_toggle_user_status, name='admin_toggle_user_status'),
    path('admin-custom/users/import/', views.admin_import_users, name='admin_import_users'),
    
    # New merchant verification and email routes
    path('admin-custom/merchants/<int:merchant_id>/update-verification/', views.admin_update_merchant_verification, name='admin_update_merchant_verification'),
    path('admin-custom/merchants/<int:merchant_id>/email/', views.admin_send_merchant_email, name='admin_send_merchant_email'),

    # Admin authentication
    path('admin-custom/login/', views.admin_login, name='admin_login'),
    path('admin-custom/logout/', views.admin_logout, name='admin_logout'),
    
    # Support ticket URLs - admin-custom version
    path('admin-custom/support/', views.admin_support_tickets, name='admin_support_tickets'),
    path('admin-custom/support/<str:ticket_id>/', views.admin_support_ticket_detail, name='admin_support_ticket_detail'),
    path('admin-custom/support/<str:ticket_id>/status/', views.admin_update_ticket_status, name='admin_update_ticket_status'),
    path('admin-custom/support/<str:ticket_id>/assign/', views.admin_assign_ticket, name='admin_assign_ticket'),

    # Transaction approval and receipt URLs for admin-custom
    path('admin-custom/transactions/<str:reference>/approve/', views.approve_transaction, name='admin_custom_approve_transaction'),
    path('admin-custom/transactions/<str:reference>/send_receipt/', views.send_transaction_receipt, name='admin_custom_send_receipt'),
    
    # Legacy admin support ticket URLs - keeping for compatibility
    path('admin/support/tickets/', views.admin_support_tickets, name='admin_support_tickets_legacy'),
    path('admin/support/tickets/<str:ticket_id>/', views.admin_support_ticket_detail, name='admin_support_ticket_detail_legacy'),
    path('admin/support/tickets/<str:ticket_id>/status/', views.admin_update_ticket_status, name='admin_update_ticket_status_legacy'),
    path('admin/support/tickets/<str:ticket_id>/assign/', views.admin_assign_ticket, name='admin_assign_ticket_legacy'),
    
    # Merchant support ticket URLs
    path('merchant/support/tickets/', views.merchant_support_tickets, name='merchant_support_tickets'),
    path('merchant/support/tickets/create/', views.merchant_create_support_ticket, name='merchant_create_support_ticket'),
    path('merchant/support/tickets/<str:ticket_id>/', views.merchant_support_ticket_detail, name='merchant_support_ticket_detail'),

    # Support Ticket API URLs
    path('api/support/tickets/', views.SupportTicketsAPIView.as_view(), name='api_support_tickets'),
    path('api/support/tickets/<str:ticket_id>/', views.SupportTicketDetailAPIView.as_view(), name='api_support_ticket_detail'),
    
    # Transaction approval and receipt sending URLs
    path('admin/transactions/<str:reference>/approve/', views.approve_transaction, name='approve_transaction'),
    path('admin/transactions/<str:reference>/send_receipt/', views.send_transaction_receipt, name='send_transaction_receipt'),
    
    # Admin Custom URLs
    path('admin-custom/', include((admin_custom_urls, 'admin_custom'))),
    
    # Standalone API
    path('api/standalone/v1/', include(standalone_api_urls)),
]