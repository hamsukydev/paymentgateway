from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count
from django.core.paginator import Paginator
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import api_view, action
from rest_framework.permissions import IsAuthenticated, AllowAny
import json
import uuid
import hmac
import hashlib
import base64
import logging
import datetime
import random
import csv
import io
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import secrets

from .models import Transaction, Merchant, Customer, PaymentMethod, Subscription, PaymentPlan, Webhook, ComplianceLog, SupportTicket, SupportTicketReply, SupportTicketNotification
from .payment_processor import get_payment_processor, StandalonePaymentProcessor
from .fraud_detector import analyze_transaction
from .serializers.transaction_serializers import TransactionSerializer
from .serializers.customer_serializers import CustomerSerializer
from .serializers.payment_plan_serializers import PaymentPlanSerializer  
from .serializers.subscription_serializers import SubscriptionSerializer
from .serializers.merchant_serializers import MerchantSerializer
from .webhook_service import process_webhook, WebhookService  # Added WebhookService import

# These are likely needed based on your code references
from .models import AnalyticsData, PaymentMethodStats
from .serializers.transaction_serializers import InitiateTransactionSerializer
from .serializers.subscription_serializers import CreateSubscriptionSerializer
from .serializers.merchant_serializers import MerchantRegistrationSerializer

logger = logging.getLogger(__name__)

# Home page view
def home_view(request):
    """View for the home page"""
    try:
        # Pass some basic context to enhance the homepage
        context = {
            'page_title': 'Modern Payment Gateway',
            'current_year': timezone.now().year
        }
        return render(request, 'payments/home.html', context)
    except Exception as e:
        # Log any errors to help with debugging
        logger.error(f"Error in home_view: {str(e)}")
        return HttpResponse("An error occurred while loading the page. Please try again later.", status=500)

# Admin Dashboard Data API
@staff_member_required
def admin_dashboard_data(request):
    # Get dashboard statistics
    total_transactions = Transaction.objects.count()
    total_customers = Customer.objects.count()
    
    # Calculate success rate
    success_count = Transaction.objects.filter(status='success').count()
    success_rate = 0
    if total_transactions > 0:
        success_rate = round((success_count / total_transactions) * 100)
    
    # Calculate total revenue (from successful transactions)
    total_revenue = Transaction.objects.filter(status='success').aggregate(
        total=Sum('amount', default=0)
    )['total'] or 0
    
    # Get recent transactions
    recent_transactions = Transaction.objects.all().order_by('-created_at')[:5]
    transactions_data = []
    for tx in recent_transactions:
        transactions_data.append({
            'reference': tx.reference,
            'email': tx.email,
            'amount': f"{tx.currency} {tx.amount}",
            'status': tx.status
        })
    
    # Get recent customers
    recent_customers = Customer.objects.all().order_by('-created_at')[:5]
    customers_data = []
    for customer in recent_customers:
        customers_data.append({
            'name': customer.name,
            'email': customer.email,
            'date': customer.created_at.strftime('%Y-%m-%d')
        })
    
    # Get revenue data for chart (last 7 months)
    today = timezone.now()
    months_data = []
    labels = []
    
    for i in range(6, -1, -1):
        # Get month date range
        month_date = today - datetime.timedelta(days=30 * i)
        month_start = month_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_date.month == 12:
            next_month = month_date.replace(year=month_date.year + 1, month=1, day=1)
        else:
            next_month = month_date.replace(month=month_date.month + 1, day=1)
        month_end = next_month - datetime.timedelta(days=1)
        month_end = month_end.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Get revenue for this month
        month_revenue = Transaction.objects.filter(
            status='success',
            created_at__gte=month_start,
            created_at__lte=month_end
        ).aggregate(
            total=Sum('amount', default=0)
        )['total'] or 0
        
        # Add to data arrays
        months_data.append(float(month_revenue))
        labels.append(month_date.strftime('%b'))
    
    return JsonResponse({
        'stats': {
            'total_transactions': total_transactions,
            'total_customers': total_customers,
            'success_rate': f"{success_rate}%",
            'total_revenue': f"₦{float(total_revenue):,.2f}",
        },
        'recent_transactions': transactions_data,
        'recent_customers': customers_data,
        'revenue_chart': {
            'labels': labels,
            'data': months_data
        }
    })

# Mock for payment processor API
class PaymentProcessor:
    @staticmethod
    def initialize_payment(data):
        # In a real-world scenario, this would call an external payment processor API
        # For now, we'll mock a successful response
        reference = data.get('reference', Transaction.generate_reference())
        
        payment_url = f"/payment/checkout/{reference}/"
        
        return {
            'status': 'success',
            'message': 'Payment initiated',
            'data': {
                'reference': reference,
                'authorization_url': payment_url,
                'access_code': f"ACCESS-{uuid.uuid4().hex[:6].upper()}"
            }
        }
        
    @staticmethod
    def verify_payment(reference):
        # In a real-world scenario, this would verify with the payment processor
        # For demo purposes, we'll assume the payment was successful
        return {
            'status': 'success',
            'message': 'Payment verified',
            'data': {
                'reference': reference,
                'status': 'success',
                'amount': 10000.00,  # This would come from the actual payment processor
                'channel': 'card',
                'currency': 'NGN',
                'transaction_date': timezone.now().isoformat(),
            }
        }


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer


class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer


class PaymentPlanViewSet(viewsets.ModelViewSet):
    queryset = PaymentPlan.objects.all()
    serializer_class = PaymentPlanSerializer


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer


class InitiatePaymentView(APIView):
    def post(self, request):
        try:
            # Log incoming request for debugging
            logger.debug(f"Payment initialization request: {request.data}")
            
            serializer = InitiateTransactionSerializer(data=request.data)
            if serializer.is_valid():
                data = serializer.validated_data
                email = data.get('email')
                amount = data.get('amount')
                currency = data.get('currency', 'NGN')
                description = data.get('description', '')
                metadata = data.get('metadata', {})
                callback_url = data.get('callback_url', request.build_absolute_uri(reverse('payments:verify-payment')))
                
                # Create or get customer
                customer, created = Customer.objects.get_or_create(
                    email=email,
                    defaults={'name': email.split('@')[0]}
                )
                
                # Create transaction record
                reference = Transaction.generate_reference()
                transaction = Transaction.objects.create(
                    reference=reference,
                    amount=amount,
                    currency=currency,
                    customer=customer,
                    email=email,
                    status='pending',
                    description=description
                )
                
                # Set metadata separately
                if metadata:
                    transaction.set_metadata(metadata)
                
                # Initialize payment with our payment processor
                payment_data = {
                    'amount': float(amount),
                    'email': email,
                    'currency': currency,
                    'reference': reference,
                    'callback_url': callback_url,
                    'metadata': metadata
                }
                
                # Call the payment processor
                result = PaymentProcessor.initialize_payment(payment_data)
                
                if result['status'] == 'success':
                    # Update transaction with payment URL
                    transaction.payment_url = result['data']['authorization_url']
                    transaction.save()
                    
                    return Response({
                        'status': 'success',
                        'message': 'Payment initialized',
                        'data': {
                            'reference': transaction.reference,
                            'amount': float(transaction.amount),
                            'authorization_url': result['data']['authorization_url'],
                            'access_code': result['data'].get('access_code', '')
                        }
                    }, status=status.HTTP_200_OK)
                else:
                    transaction.status = 'failed'
                    transaction.save()
                    
                    return Response({
                        'status': 'error',
                        'message': 'Could not initialize payment',
                        'data': result.get('data', {})
                    }, status=status.HTTP_400_BAD_REQUEST)
            
            return Response({
                'status': 'error',
                'message': 'Invalid request data',
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Payment initialization error: {str(e)}")
            return Response({
                'status': 'error',
                'message': 'An error occurred during payment initialization'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Mobile Money Payment Processing
class MobileMoneyPaymentView(APIView):
    def post(self, request):
        reference = request.data.get('transaction_reference')
        mobile_number = request.data.get('mobile_number')
        email = request.data.get('email')
        provider = request.data.get('provider')

        if not all([reference, mobile_number, email, provider]):
            return Response({
                'status': 'error',
                'message': 'Missing required fields'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # In a real implementation, this would integrate with the mobile money provider's API
        # For now, we'll simulate the request being sent to the customer's phone

        # Update transaction with mobile payment info
        transaction.payment_method = f'mobile_money_{provider}'
        transaction.save()

        # Store mobile number in metadata
        metadata = transaction.get_metadata() or {}
        metadata['mobile_number'] = mobile_number
        metadata['provider'] = provider
        transaction.set_metadata(metadata)
        transaction.save()

        # In production, we would send a real payment request to the mobile money provider
        # and they would send a request to the customer's phone

        return Response({
            'status': 'success',
            'message': f'Payment request sent to your {provider.upper()} mobile money account. Please check your phone to authorize.',
            'data': {
                'reference': transaction.reference,
                'provider': provider
            }
        })


# Mobile Money Verification
class VerifyMobilePaymentView(APIView):
    def post(self, request):
        reference = request.data.get('transaction_reference')
        
        if not reference:
            return Response({
                'status': 'error',
                'message': 'Missing transaction reference'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # In a real implementation, we would check with the mobile money provider's API
        # For now, we'll simulate a successful payment 50% of the time for demo purposes
        import random
        is_successful = random.choice([True, False])

        if is_successful:
            transaction.status = 'success'
            transaction.save()
            
            return Response({
                'status': 'success',
                'message': 'Mobile money payment verified successfully',
                'redirect_url': reverse('payment_success', args=[transaction.reference])
            })
        else:
            return Response({
                'status': 'error',
                'message': 'Payment verification still pending. Please try again in a few moments.'
            }, status=status.HTTP_400_BAD_REQUEST)


# QR Code Payment Verification
class VerifyQRPaymentView(APIView):
    def post(self, request):
        reference = request.data.get('transaction_reference')
        
        if not reference:
            return Response({
                'status': 'error',
                'message': 'Missing transaction reference'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # In a real implementation, we would check with the banking API
        # For now, we'll simulate a successful payment 50% of the time for demo purposes
        import random
        is_successful = random.choice([True, False])

        if is_successful:
            transaction.status = 'success'
            transaction.payment_method = 'qr_code'
            transaction.save()
            
            return Response({
                'status': 'success',
                'message': 'QR code payment verified successfully',
                'redirect_url': reverse('payment_success', args=[transaction.reference])
            })
        else:
            return Response({
                'status': 'error',
                'message': 'Payment verification still pending. Please try again in a few moments.'
            }, status=status.HTTP_400_BAD_REQUEST)


# Bank Transfer Verification
class VerifyTransferPaymentView(APIView):
    def post(self, request):
        reference = request.data.get('transaction_reference')
        
        if not reference:
            return Response({
                'status': 'error',
                'message': 'Missing transaction reference'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # In a real implementation, this would check the bank's API for a transfer matching
        # the transaction reference
        
        # For demo purposes, simulate a 50% chance of verification success
        import random
        is_successful = random.choice([True, False])

        if is_successful:
            transaction.status = 'success'
            transaction.payment_method = 'bank_transfer'
            transaction.save()
            
            return Response({
                'status': 'success',
                'message': 'Bank transfer verified successfully',
                'redirect_url': reverse('payment_success', args=[transaction.reference])
            })
        else:
            return Response({
                'status': 'error',
                'message': 'Transfer verification still pending. Please try again in a few moments.'
            }, status=status.HTTP_400_BAD_REQUEST)


# USSD Payment Verification
class VerifyUSSDPaymentView(APIView):
    def post(self, request):
        reference = request.data.get('transaction_reference')
        
        if not reference:
            return Response({
                'status': 'error',
                'message': 'Missing transaction reference'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # In a real implementation, this would check with the telecom provider's API
        
        # For demo purposes, simulate a 50% chance of verification success
        import random
        is_successful = random.choice([True, False])

        if is_successful:
            transaction.status = 'success'
            transaction.payment_method = 'ussd'
            transaction.save()
            
            return Response({
                'status': 'success',
                'message': 'USSD payment verified successfully',
                'redirect_url': reverse('payment_success', args=[transaction.reference])
            })
        else:
            return Response({
                'status': 'error',
                'message': 'USSD payment verification still pending. Please try again in a few moments.'
            }, status=status.HTTP_400_BAD_REQUEST)


class VerifyPaymentView(APIView):
    def get(self, request):
        reference = request.GET.get('reference')
        if not reference:
            return Response({
                'status': 'error',
                'message': 'No reference provided'
            }, status=status.HTTP_400_BAD_REQUEST)
            
        # Get transaction
        try:
            transaction = Transaction.objects.get(reference=reference)
        except Transaction.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Transaction not found'
            }, status=status.HTTP_404_NOT_FOUND)
            
        # Call payment processor to verify
        result = PaymentProcessor.verify_payment(reference)
        
        if result['status'] == 'success' and result['data']['status'] == 'success':
            # Update transaction
            transaction.status = 'success'
            transaction.payment_method = result['data'].get('channel', '')
            transaction.save()
            
            return Response({
                'status': 'success',
                'message': 'Payment verified successfully',
                'data': {
                    'reference': transaction.reference,
                    'amount': float(transaction.amount),
                    'status': transaction.status,
                    'email': transaction.email
                }
            }, status=status.HTTP_200_OK)
        else:
            transaction.status = 'failed'
            transaction.save()
            return Response({
                'status': 'error',
                'message': 'Payment verification failed'
            }, status=status.HTTP_400_BAD_REQUEST)


class CreateSubscriptionView(APIView):
    def post(self, request):
        serializer = CreateSubscriptionSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            email = data['email']
            plan_id = data['plan_id']
            start_date = data.get('start_date', timezone.now())
            
            # Get or create customer
            try:
                customer = Customer.objects.get(email=email)
            except Customer.DoesNotExist:
                return Response({
                    'status': 'error',
                    'message': 'Customer with this email does not exist'
                }, status=status.HTTP_404_NOT_FOUND)
                
            # Get plan
            try:
                plan = PaymentPlan.objects.get(id=plan_id, active=True)
            except PaymentPlan.DoesNotExist:
                return Response({
                    'status': 'error',
                    'message': 'Plan not found or inactive'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Calculate next payment date based on plan interval
            next_payment_date = start_date
            if plan.interval == 'daily':
                next_payment_date = start_date + timezone.timedelta(days=1)
            elif plan.interval == 'weekly':
                next_payment_date = start_date + timezone.timedelta(days=7)
            elif plan.interval == 'monthly':
                next_payment_date = start_date + timezone.timedelta(days=30)
            elif plan.interval == 'quarterly':
                next_payment_date = start_date + timezone.timedelta(days=90)
            elif plan.interval == 'annually':
                next_payment_date = start_date + timezone.timedelta(days=365)
                
            # Create subscription
            subscription = Subscription.objects.create(
                customer=customer,
                plan=plan,
                reference=f"SUB-{uuid.uuid4().hex[:8].upper()}",
                status='active',
                next_payment_date=next_payment_date
            )
            
            # Return subscription details
            return Response({
                'status': 'success',
                'message': 'Subscription created successfully',
                'data': SubscriptionSerializer(subscription).data
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Payment Checkout Views (Web UI)
def payment_checkout(request, reference):
    try:
        transaction = Transaction.objects.get(reference=reference)
        return render(request, 'payments/checkout.html', {
            'transaction': transaction,
            'amount': float(transaction.amount),
            'email': transaction.email,
            'reference': transaction.reference
        })
    except Transaction.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Invalid transaction reference'
        }, status=404)


def payment_success(request, reference):
    try:
        transaction = Transaction.objects.get(reference=reference)
        return render(request, 'payments/success.html', {
            'transaction': transaction
        })
    except Transaction.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Invalid transaction reference'
        }, status=404)


# Merchant Registration API
class MerchantRegistrationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = MerchantRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            merchant = serializer.save()
            return Response({
                'status': 'success',
                'message': 'Merchant account created successfully. Your account is pending verification.',
                'data': {
                    'business_name': merchant.business_name,
                    'business_email': merchant.business_email,
                    'public_key': merchant.public_key,
                    'verification_status': merchant.verification_status
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MerchantViewSet(viewsets.ModelViewSet):
    queryset = Merchant.objects.all()
    serializer_class = MerchantSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Merchant.objects.all()
        return Merchant.objects.filter(user=user)
    
        if total_transactions > 0:
            success_rate = round((success_count / total_transactions) * 100)
        
        # Calculate total revenue
        total_revenue = Transaction.objects.filter(merchant=merchant, status='success').aggregate(
            total=Sum('amount', default=0)
        )['total'] or 0
        
        # Get recent transactions
        recent_transactions = Transaction.objects.filter(merchant=merchant).order_by('-created_at')[:5]
        
        # Get customers who have made transactions with this merchant
        customer_count = Transaction.objects.filter(merchant=merchant).values('customer').distinct().count()
        
        return Response({
            'total_transactions': total_transactions,
            'success_rate': f"{success_rate}%",
            'total_revenue': float(total_revenue),
            'customer_count': customer_count,
            'recent_transactions': TransactionSerializer(recent_transactions, many=True).data
        })
    
    @action(detail=False, methods=['get'])
    def api_keys(self, request):
        try:
            merchant = Merchant.objects.get(user=request.user)
            return Response({
                'public_key': merchant.public_key,
                'secret_key': merchant.secret_key if request.GET.get('reveal_secret') == 'true' else 'sk_****' + merchant.secret_key[-4:]
            })
        except Merchant.DoesNotExist:
            return Response(
                {"detail": "Merchant profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=False, methods=['post'])
    def regenerate_keys(self, request):
        try:
            merchant = Merchant.objects.get(user=request.user)
            merchant.public_key = Merchant.generate_public_key()
            merchant.secret_key = Merchant.generate_secret_key()
            merchant.save()
            
            return Response({
                'message': 'API keys regenerated successfully',
                'public_key': merchant.public_key,
                'secret_key': merchant.secret_key
            })
        except Merchant.DoesNotExist:
            return Response(
                {"detail": "Merchant profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND
            )


# Merchant Web Views
def merchant_register(request):
    if request.method == 'POST':
        # Get form data
        business_name = request.POST.get('business_name')
        business_type = request.POST.get('business_type')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')
        phone = request.POST.get('phone', '')  # Making phone optional
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password', password)  # Confirm password might not exist in form
        terms = request.POST.get('terms')
        
        # Validate form data
        error = None
        if not all([business_name, first_name, last_name, email, password, terms]):
            error = "Please fill in all required fields"
        elif password != confirm_password:
            error = "Passwords do not match"
        elif len(password) < 8:
            error = "Password must be at least 8 characters long"
        
        # Check if user with this email already exists
        from django.contrib.auth.models import User
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            error = "A user with this email already exists"
            
        # Check if merchant with this email already exists
        if Merchant.objects.filter(business_email=email).exists():
            error = "A merchant account with this email already exists"
            
        if error:
            return render(request, 'payments/merchant_register.html', {'error': error})
            
        try:
            # Create user account
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name
            )
            
            # Create merchant account
            merchant = Merchant.objects.create(
                user=user,
                business_name=business_name,
                business_email=email,
                business_phone=phone or "",
                business_address=request.POST.get('business_address', ""),  # Handle optional fields
                industry=business_type,
                public_key=Merchant.generate_public_key(),
                secret_key=Merchant.generate_secret_key(),
                verification_status='pending'
            )
            
            # Send welcome email
            try:
                from .email_service import EmailService
                EmailService.send_merchant_welcome_email(merchant)
                logger.info(f"Sent welcome email to new merchant: {email}")
            except Exception as e:
                logger.error(f"Failed to send welcome email: {str(e)}")
            
            # Log in the user
            user = authenticate(request, username=email, password=password)
            
            if user is not None:
                login(request, user)
                
            # Redirect to dashboard
            return redirect('payments:merchant_dashboard')
            
        except Exception as e:
            print(f"Error creating merchant account: {str(e)}")
            error = "There was a problem creating your account. Please try again."
            return render(request, 'payments/merchant_register.html', {'error': error})
    
    return render(request, 'payments/merchant_register.html')


@login_required
def merchant_dashboard(request):
    try:
        print(f"Looking for merchant with user_id: {request.user.id}, username: {request.user.username}")
        merchant = Merchant.objects.get(user=request.user)
        return render(request, 'payments/merchant_dashboard.html', {
            'merchant': merchant
        })
    except Merchant.DoesNotExist:
        print(f"No merchant found for user {request.user.username} (ID: {request.user.id}). Redirecting to registration.")
        # Instead of redirecting to registration, let's create a merchant profile
        from django.contrib.auth.models import User
        try:
            merchant = Merchant.objects.create(
                user=request.user,
                business_name=f"{request.user.first_name} {request.user.last_name}'s Business",
                business_email=request.user.email,
                business_phone="",
                business_address="",
                industry="Other",
                public_key=Merchant.generate_public_key(),
                secret_key=Merchant.generate_secret_key(),
                verification_status='pending'
            )
            print(f"Created new merchant profile for {request.user.username}")
            return render(request, 'payments/merchant_dashboard.html', {
                'merchant': merchant
            })
        except Exception as creation_error:
            print(f"Failed to create merchant profile: {str(creation_error)}")
            return redirect('payments:merchant_register')
    except Exception as e:
        # If there's a database error (like table doesn't exist), redirect to register
        print(f"Error accessing merchant dashboard: {str(e)}")
        return redirect('payments:merchant_register')


@login_required
def merchant_transactions(request):
    try:
        merchant = Merchant.objects.get(user=request.user)
        transactions = Transaction.objects.filter(merchant=merchant).order_by('-created_at')
        return render(request, 'payments/merchant_transactions.html', {
            'merchant': merchant,
            'transactions': transactions
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_settings(request):
    try:
        merchant = Merchant.objects.get(user=request.user)
        return render(request, 'payments/merchant_settings.html', {
            'merchant': merchant
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_api_docs(request):
    try:
        merchant = Merchant.objects.get(user=request.user)
        return render(request, 'payments/merchant_api_docs.html', {
            'merchant': merchant
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def analytics_view(request):
    """View for detailed merchant analytics dashboard"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        return render(request, 'payments/merchant_analytics.html', {
            'merchant': merchant
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


class AnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response(
                {"detail": "Merchant profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get query parameters
        timeframe = request.query_params.get('timeframe', '30days')
        start_date_str = request.query_params.get('start_date')
        end_date_str = request.query_params.get('end_date')
        
        # Calculate date range based on timeframe or custom dates
        end_date = timezone.now().date()
        
        if start_date_str and end_date_str:
            try:
                start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {"detail": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            if timeframe == '7days':
                start_date = end_date - datetime.timedelta(days=7)
            elif timeframe == '30days':
                start_date = end_date - datetime.timedelta(days=30)
            elif timeframe == '90days':
                start_date = end_date - datetime.timedelta(days=90)
            elif timeframe == '1year':
                start_date = end_date - datetime.timedelta(days=365)
            else:
                return Response(
                    {"detail": "Invalid timeframe. Use 7days, 30days, 90days, or 1year."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Get transactions for the date range
        transactions = Transaction.objects.filter(
            merchant=merchant,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        )
        
        # Calculate summary statistics
        total_transactions = transactions.count()
        successful_transactions = transactions.filter(status='success').count()
        failed_transactions = transactions.filter(status='failed').count()
        pending_transactions = transactions.filter(status='pending').count()
        
        success_rate = 0
        if total_transactions > 0:
            success_rate = round((successful_transactions / total_transactions) * 100)
        
        # Calculate total volume by currency
        volumes_by_currency = {}
        for tx in transactions.filter(status='success'):
            if tx.currency not in volumes_by_currency:
                volumes_by_currency[tx.currency] = 0
            volumes_by_currency[tx.currency] += float(tx.amount)
        
        # Get payment method breakdown
        payment_methods = transactions.filter(status='success').values('payment_method').annotate(
            count=Count('id'),
            volume=Sum('amount')
        ).order_by('-count')
        
        # Get new customers in the time period
        new_customers_count = Customer.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
            transactions__merchant=merchant
        ).distinct().count()
        
        # Prepare time series data for chart
        time_series_data = self._get_time_series_data(
            transactions, start_date, end_date, timeframe
        )
        
        # Get daily analytics if available
        analytics_data = AnalyticsData.objects.filter(
            merchant=merchant,
            date__gte=start_date,
            date__lte=end_date
        ).order_by('date')
        
        analytics_by_date = {}
        for data in analytics_data:
            date_str = data.date.isoformat()
            if date_str not in analytics_by_date:
                analytics_by_date[date_str] = {}
            analytics_by_date[date_str][data.currency] = {
                'total_transactions': data.total_transactions,
                'successful_transactions': data.successful_transactions,
                'failed_transactions': data.failed_transactions,
                'total_volume': float(data.total_volume),
                'new_customers': data.new_customers
            }
        
        # Get payment method stats if available
        payment_method_stats = PaymentMethodStats.objects.filter(
            merchant=merchant,
            date__gte=start_date,
            date__lte=end_date
        )
        
        payment_method_breakdown = {}
        for stat in payment_method_stats:
            if stat.payment_method not in payment_method_breakdown:
                payment_method_breakdown[stat.payment_method] = {
                    'count': 0,
                    'volume': 0
                }
            payment_method_breakdown[stat.payment_method]['count'] += stat.transaction_count
            payment_method_breakdown[stat.payment_method]['volume'] += float(stat.volume)
        
        # Compile response
        response_data = {
            'summary': {
                'total_transactions': total_transactions,
                'successful_transactions': successful_transactions,
                'failed_transactions': failed_transactions,
                'pending_transactions': pending_transactions,
                'success_rate': f"{success_rate}%",
                'volumes_by_currency': volumes_by_currency,
                'new_customers': new_customers_count
            },
            'payment_methods': list(payment_methods),
            'time_series': time_series_data,
            'analytics_by_date': analytics_by_date,
            'payment_method_breakdown': payment_method_breakdown,
            'date_range': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            }
        }
        
        return Response(response_data)
    
    def _get_time_series_data(self, transactions, start_date, end_date, timeframe):
        if timeframe == '7days':
            date_format = '%Y-%m-%d'
            date_field = 'day'
        elif timeframe == '30days':
            date_format = '%Y-%m-%d'
            date_field = 'day'
        elif timeframe == '90days':
            date_format = '%Y-%W'
            date_field = 'week'
        elif timeframe == '1year':
            date_format = '%b %Y'
            date_field = 'month'
        
        # Create a dictionary to store data by date
        data_by_date = {}
        
        # Create all dates in the range as keys
        current_date = start_date
        while current_date <= end_date:
            if date_field == 'day':
                key = current_date.strftime(date_format)
                data_by_date[key] = {
                    'date': current_date.isoformat(),
                    'count': 0,
                    'volume': 0,
                    'success_count': 0,
                    'failed_count': 0
                }
            elif date_field == 'week':
                key = current_date.strftime(date_format)
                # Only create a new entry if this is the start of a week
                if key not in data_by_date:
                    data_by_date[key] = {
                        'date': current_date.isoformat(),
                        'count': 0,
                        'volume': 0,
                        'success_count': 0,
                        'failed_count': 0
                    }
            elif date_field == 'month':
                key = current_date.strftime(date_format)
                # Only create a new entry if this is the start of a month
                if key not in data_by_date:
                    data_by_date[key] = {
                        'date': current_date.isoformat(),
                        'count': 0,
                        'volume': 0,
                        'success_count': 0,
                        'failed_count': 0
                    }
            
            current_date += datetime.timedelta(days=1)
        
        # Populate with actual transaction data
        for tx in transactions:
            tx_date = tx.created_at.date()
            if date_field == 'day':
                key = tx_date.strftime(date_format)
            elif date_field == 'week':
                key = tx_date.strftime(date_format)
            elif date_field == 'month':
                key = tx_date.strftime(date_format)
            
            if key in data_by_date:
                data_by_date[key]['count'] += 1
                
                if tx.status == 'success':
                    data_by_date[key]['success_count'] += 1
                    data_by_date[key]['volume'] += float(tx.amount)
                elif tx.status == 'failed':
                    data_by_date[key]['failed_count'] += 1
        
        # Convert to a list of ordered data points
        ordered_keys = sorted(data_by_date.keys())
        time_series = [data_by_date[k] for k in ordered_keys]
        
        return {
            'labels': ordered_keys,
            'datasets': {
                'volume': [data['volume'] for data in time_series],
                'count': [data['count'] for data in time_series],
                'success_count': [data['success_count'] for data in time_series],
                'failed_count': [data['failed_count'] for data in time_series]
            }
        }


@api_view(['POST'])
def handle_webhook(request, provider):
    """
    Handle incoming webhooks from payment providers
    
    Args:
        request: Django request object
        provider: Provider name ('paystack', 'flutterwave', etc.)
    """
    # Get request data and headers
    request_data = request.body
    headers = request.headers
    
    # Log webhook received
    logger.info(f"Received webhook from {provider}")
    
    # Process webhook
    result = process_webhook(request_data, headers, provider)
    
    # Return appropriate response
    if result.get('success'):
        logger.info(f"Successfully processed {provider} webhook for transaction {result.get('transaction_reference')}")
        return Response({"status": "success"}, status=status.HTTP_200_OK)
    else:
        logger.warning(f"Failed to process {provider} webhook: {result.get('error')}")
        return Response(
            {"status": "error", "message": result.get('error', 'Unknown error')},
            status=status.HTTP_400_BAD_REQUEST
        )


def merchant_login(request):
    """View to handle merchant login"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        remember_me = request.POST.get('remember_me')
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            
            # Set session expiry if remember_me is checked
            if remember_me:
                # Set session to expire in 30 days
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                # Default session expiry (when browser closes)
                request.session.set_expiry(0)
                
            # Redirect to merchant dashboard after login
            return redirect('payments:merchant_dashboard')
        else:
            # Authentication failed
            return render(request, 'payments/merchant_login.html', {
                'error': 'Invalid email or password. Please try again.'
            })
    else:
        # GET request - show login form
        return render(request, 'payments/merchant_login.html')


@login_required
def merchant_update_profile(request):
    """View to handle merchant profile updates"""
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
        try:
            merchant = Merchant.objects.get(user=request.user)
            
            if form_type == 'profile':
                # Update user profile
                first_name = request.POST.get('first_name')
                last_name = request.POST.get('last_name')
                email = request.POST.get('email')
                
                # Update user object
                user = request.user
                user.first_name = first_name
                user.last_name
                
                # Only update email if it changed
                if email != user.email:
                    # Check if email is already in use
                    from django.contrib.auth.models import User
                    if User.objects.exclude(id=user.id).filter(email=email).exists():
                        return render(request, 'payments/merchant_settings.html', {
                            'merchant': merchant,
                            'error': 'Email is already in use'
                        })
                    user.email = email
                    user.username = email  # Assuming username is email
                
                user.save()
                
                # Set success message
                from django.contrib import messages
                messages.success(request, 'Profile updated successfully!')
                
            elif form_type == 'business':
                # Update business details
                merchant.business_name = request.POST.get('business_name')
                merchant.business_email = request.POST.get('business_email')
                merchant.business_phone = request.POST.get('business_phone')
                merchant.business_address = request.POST.get('business_address')
                merchant.website = request.POST.get('website')
                merchant.industry = request.POST.get('industry')
                merchant.business_description = request.POST.get('business_description')
                
                merchant.save()
                
                # Set success message
                from django.contrib import messages
                messages.success(request, 'Business details updated successfully!')
                
            elif form_type == 'payment':
                # Update payment settings
                merchant.settlement_bank = request.POST.get('settlement_bank')
                merchant.settlement_account = request.POST.get('settlement_account')
                merchant.settlement_account_name = request.POST.get('settlement_account_name')
                
                merchant.save()
                
                # Set success message
                from django.contrib import messages
                messages.success(request, 'Payment settings updated successfully!')
            
            return redirect('payments:merchant_settings')
            
        except Merchant.DoesNotExist:
            return redirect('payments:merchant_register')
        except Exception as e:
            # Handle unexpected errors
            print(f"Error updating profile: {str(e)}")
            from django.contrib import messages
            messages.error(request, 'An error occurred while updating your profile. Please try again.')
            return redirect('payments:merchant_settings')
    
    # GET requests should redirect to settings page
    return redirect('payments:merchant_settings')


@login_required
def merchant_update_password(request):
    """View to handle merchant password update"""
    if request.method == 'POST':
        try:
            merchant = Merchant.objects.get(user=request.user)
            current_password = request.POST.get('current_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            # Check if current password is correct
            from django.contrib.auth import authenticate
            user = authenticate(username=request.user.username, password=current_password)
            
            if user is None:
                # Current password is incorrect
                from django.contrib import messages
                messages.error(request, 'Current password is incorrect.')
                return redirect('payments:merchant_settings')
            
            # Check if new passwords match
            if new_password != confirm_password:
                from django.contrib import messages
                messages.error(request, 'New passwords do not match.')
                return redirect('payments:merchant_settings')
            
            # Update password
            user.set_password(new_password)
            user.save()
            
            # Log the user in again with new password
            from django.contrib.auth import login
            login(request, user)
            
            # Send password change notification email
            try:
                from .email_service import EmailService
                EmailService.send_password_changed_notification(merchant)
                logger.info(f"Sent password changed notification to merchant: {merchant.business_email}")
            except Exception as e:
                logger.error(f"Failed to send password changed notification: {str(e)}")
            
            # Set success message
            from django.contrib import messages
            messages.success(request, 'Password updated successfully!')
            
            return redirect('payments:merchant_settings')
            
        except Merchant.DoesNotExist:
            return redirect('payments:merchant_register')
        except Exception as e:
            # Handle unexpected errors
            print(f"Error updating password: {str(e)}")
            from django.contrib import messages
            messages.error(request, 'An error occurred while updating your password. Please try again.')
            return redirect('payments:merchant_settings')
    
    # GET requests should redirect to settings page
    return redirect('payments:merchant_settings')


@login_required
def merchant_api_keys(request):
    """View to get merchant API keys"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        reveal_secret = request.GET.get('reveal_secret') == 'true'
        
        if reveal_secret:
            # Return full secret key
            return JsonResponse({
                'public_key': merchant.public_key,
                'secret_key': merchant.secret_key
            })
        else:
            # Return masked secret key
            return JsonResponse({
                'public_key': merchant.public_key,
                'secret_key': 'sk_****' + merchant.secret_key[-4:]
            })
            
    except Merchant.DoesNotExist:
        return JsonResponse({
            'error': 'Merchant account not found'
        }, status=404)
    except Exception as e:
        print(f"Error fetching API keys: {str(e)}")
        return JsonResponse({
            'error': 'An error occurred'
        }, status=500)


@login_required
def merchant_regenerate_keys(request):
    """View to regenerate merchant API keys"""
    if request.method == 'POST':
        try:
            merchant = Merchant.objects.get(user=request.user)
            
            # Generate new keys
            merchant.public_key = Merchant.generate_public_key()
            merchant.secret_key = Merchant.generate_secret_key()
            merchant.save()
            
            return JsonResponse({
                'success': True,
                'public_key': merchant.public_key,
                'secret_key': merchant.secret_key
            })
            
        except Merchant.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Merchant account not found'
            }, status=404)
        except Exception as e:
            print(f"Error regenerating API keys: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': 'An error occurred'
            }, status=500)
    
    return JsonResponse({
        'success': False,
        'message': 'Invalid request method'
    }, status=400)


@login_required
def merchant_delete_account(request):
    """View to delete merchant account"""
    if request.method == 'POST':
        try:
            merchant = Merchant.objects.get(user=request.user)
            user = request.user
            
            # Delete merchant account
            merchant.delete()
            
            # Delete user account
            user.delete()
            
            # Return success response
            return JsonResponse({
                'success': True
            })
            
        except Merchant.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Merchant account not found'
            }, status=404)
        except Exception as e:
            print(f"Error deleting account: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': 'An error occurred'
            }, status=500)
    
    return JsonResponse({
        'success': False,
        'message': 'Invalid request method'
    }, status=400)


# New Direct Payment Provider API
class HamsukyPayAPI:
    """
    Direct payment processing API endpoints for HamsukyPay as a standalone payment provider
    """
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def initialize_payment(request):
        """
        Initialize a new payment
        
        Endpoint: /api/v1/payments/initialize/
        Method: POST
        
        Required parameters:
        - amount: Payment amount
        - email: Customer email
        - currency: Three-letter currency code (e.g. USD, NGN)
        
        Optional parameters:
        - callback_url: URL to redirect after payment
        - metadata: Additional information
        - payment_method: Payment method to use
        - description: Payment description
        - reference: Custom reference
        - customer_id: Identifier for the customer
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Validate required parameters
            required_fields = ['amount', 'email', 'currency']
            for field in required_fields:
                if field not in data:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Missing required parameter: {field}'
                    }, status=400)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
                
                # Check if merchant account is active
                if not merchant.is_active:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Merchant account is inactive'
                    }, status=403)
                    
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Initialize payment using payment processor
            processor = get_payment_processor(merchant)
            data['merchant'] = merchant
            
            # Look up customer if customer_id is provided
            customer_id = data.get('customer_id')
            if customer_id:
                try:
                    customer = Customer.objects.get(
                        external_id=customer_id,
                        merchant=merchant
                    )
                    data['customer'] = customer
                except Customer.DoesNotExist:
                    # Will create new customer based on email
                    pass
            
            # Process the payment initialization
            result = processor.initialize_payment(data)
            
            # Return result
            if result.get('status') == 'success':
                return JsonResponse(result, status=200)
            else:
                return JsonResponse(result, status=400)
                
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Payment initialization error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def verify_payment(request, reference):
        """
        Verify payment status
        
        Endpoint: /api/v1/payments/verify/<reference>/
        Method: POST
        """
        try:
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Get transaction
            try:
                transaction = Transaction.objects.get(
                    reference=reference,
                    merchant=merchant
                )
            except Transaction.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Transaction not found: {reference}'
                }, status=404)
            
            # Get processor and verify payment
            processor = get_payment_processor(merchant)
            result = processor.verify_payment(reference)
            
            return JsonResponse(result, status=200)
            
        except Exception as e:
            logger.error(f"Payment verification error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def process_payment(request, reference):
        """
        Process a payment with payment details
        
        Endpoint: /api/v1/payments/process/<reference>/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Get transaction
            try:
                transaction = Transaction.objects.get(
                    reference=reference,
                    merchant=merchant
                )
            except Transaction.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Transaction not found: {reference}'
                }, status=404)
            
            # Check if transaction can be processed
            if transaction.status != 'pending':
                return JsonResponse({
                    'status': 'error',
                    'message': f'Transaction cannot be processed. Current status: {transaction.status}'
                }, status=400)
            
            # Get payment details
            payment_details = data.get('payment_details', {})
            if not payment_details:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Payment details are required'
                }, status=400)
            
            # Process payment
            processor = get_payment_processor(merchant)
            result = processor.process_payment(reference, payment_details)
            
            # If payment was successful, run fraud detection
            if result.get('status') == 'success':
                # Get IP address and device fingerprint for fraud detection
                ip_address = request.META.get('HTTP_X_FORWARDED_FOR', '')
                if not ip_address:
                    ip_address = request.META.get('REMOTE_ADDR', '')
                    
                device_fingerprint = data.get('device_fingerprint', '')
                
                # Run async fraud detection (would be implemented as a background task)
                analyze_transaction(transaction, ip=ip_address, device_fingerprint=device_fingerprint)
            
            return JsonResponse(result, status=200)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Payment processing error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
            
    @staticmethod
    @csrf_exempt
    @require_POST
    def create_payment_plan(request):
        """
        Create a recurring payment plan
        
        Endpoint: /api/v1/payments/plans/create/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Validate required parameters
            required_fields = ['name', 'amount', 'currency', 'interval']
            for field in required_fields:
                if field not in data:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Missing required parameter: {field}'
                    }, status=400)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Validate interval
            interval = data.get('interval')
            valid_intervals = ['daily', 'weekly', 'monthly', 'quarterly', 'annually']
            if interval not in valid_intervals:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Invalid interval. Must be one of: {", ".join(valid_intervals)}'
                }, status=400)
            
            # Create payment plan
            from .models import PaymentPlan
            
            plan = PaymentPlan(
                merchant=merchant,
                name=data.get('name'),
                description=data.get('description', ''),
                amount=Decimal(str(data.get('amount'))),
                currency=data.get('currency'),
                interval=data.get('interval'),
                is_active=data.get('is_active', True)
            )
            
            # Set metadata if provided
            metadata = data.get('metadata')
            if metadata:
                plan.set_metadata(metadata)
            
            plan.save()
            
            return JsonResponse({
                'status': 'success',
                'message': 'Payment plan created successfully',
                'data': {
                    'id': str(plan.id),
                    'name': plan.name,
                    'amount': float(plan.amount),
                    'currency': plan.currency,
                    'interval': plan.interval,
                    'is_active': plan.is_active
                }
            }, status=201)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Payment plan creation error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def create_customer(request):
        """
        Create or update a customer
        
        Endpoint: /api/v1/customers/create/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Validate required parameters
            required_fields = ['email']
            for field in required_fields:
                if field not in data:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Missing required parameter: {field}'
                    }, status=400)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Generate external ID if not provided
            external_id = data.get('external_id')
            if not external_id:
                external_id = f"cust_{uuid.uuid4().hex[:16]}"
            
            # Create or update customer
            customer, created = Customer.objects.update_or_create(
                email=data.get('email'),
                merchant=merchant,
                defaults={
                    'name': data.get('name', ''),
                    'phone': data.get('phone', ''),
                    'external_id': external_id
                }
            )
            
            # Set metadata if provided
            metadata = data.get('metadata')
            if metadata:
                customer.set_metadata(metadata)
                customer.save()
            
            return JsonResponse({
                'status': 'success',
                'message': 'Customer created successfully' if created else 'Customer updated successfully',
                'data': {
                    'id': str(customer.id),
                    'email': customer.email,
                    'name': customer.name,
                    'phone': customer.phone,
                    'external_id': customer.external_id,
                    'created_at': customer.created_at.isoformat()
                }
            }, status=201 if created else 200)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Customer creation error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def create_subscription(request):
        """
        Create a subscription
        
        Endpoint: /api/v1/payments/subscriptions/create/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Validate required parameters
            required_fields = ['customer', 'plan']
            for field in required_fields:
                if field not in data:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Missing required parameter: {field}'
                    }, status=400)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Find customer
            customer_identifier = data.get('customer')
            try:
                # First try by ID
                customer = Customer.objects.get(
                    id=customer_identifier,
                    merchant=merchant
                )
            except (Customer.DoesNotExist, ValueError):
                # Then try by email
                try:
                    customer = Customer.objects.get(
                        email=customer_identifier,
                        merchant=merchant
                    )
                except Customer.DoesNotExist:
                    try:
                        customer = Customer.objects.get(
                            external_id=customer_identifier,
                            merchant=merchant
                        )
                    except Customer.DoesNotExist:
                        return JsonResponse({
                            'status': 'error',
                            'message': f'Customer not found: {customer_identifier}'
                        }, status=404)
            
            # Find payment plan
            plan_identifier = data.get('plan')
            from .models import PaymentPlan
            try:
                # First try by ID
                plan = PaymentPlan.objects.get(
                    id=plan_identifier,
                    merchant=merchant
                )
            except (PaymentPlan.DoesNotExist, ValueError):
                # Then try by name
                try:
                    plan = PaymentPlan.objects.get(
                        name=plan_identifier,
                        merchant=merchant
                    )
                except PaymentPlan.DoesNotExist:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Payment plan not found: {plan_identifier}'
                    }, status=404)
            
            # Create subscription
            from .models import Subscription
            
            # Generate reference
            reference = f"sub_{uuid.uuid4().hex[:16]}"
            
            # Calculate next payment date
            start_date = timezone.now()
            if data.get('start_date'):
                try:
                    start_date = datetime.fromisoformat(data.get('start_date').replace('Z', '+00:00'))
                except ValueError:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Invalid start_date format. Use ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).'
                    }, status=400)
            
            # Create subscription
            subscription = Subscription(
                customer=customer,
                plan=plan,
                reference=reference,
                status='active',
                start_date=start_date,
                next_payment_date=start_date
            )
            
            # Set metadata if provided
            metadata = data.get('metadata')
            if metadata:
                subscription.set_metadata(metadata)
            
            subscription.save()
            
            # Create initial transaction if requested
            if data.get('charge_immediately', False):
                processor = get_payment_processor(merchant)
                success, transaction = processor.process_subscription_payment(subscription)
                
                # Update next payment date
                if plan.interval == 'daily':
                    next_date = start_date + timedelta(days=1)
                elif plan.interval == 'weekly':
                    next_date = start_date + timedelta(weeks=1)
                elif plan.interval == 'monthly':
                    next_date = start_date + timedelta(days=30)
                elif plan.interval == 'quarterly':
                    next_date = start_date + timedelta(days=90)
                else:  # annually
                    next_date = start_date + timedelta(days=365)
                
                subscription.next_payment_date = next_date
                subscription.save()
                
                # Include transaction info in response
                transaction_info = {
                    'reference': transaction.reference,
                    'status': transaction.status,
                    'amount': float(transaction.amount),
                    'currency': transaction.currency
                }
            else:
                transaction_info = None
            
            return JsonResponse({
                'status': 'success',
                'message': 'Subscription created successfully',
                'data': {
                    'id': str(subscription.id),
                    'reference': subscription.reference,
                    'status': subscription.status,
                    'start_date': subscription.start_date.isoformat(),
                    'next_payment_date': subscription.next_payment_date.isoformat(),
                    'plan': {
                        'id': str(plan.id),
                        'name': plan.name,
                        'amount': float(plan.amount),
                        'currency': plan.currency,
                        'interval': plan.interval
                    },
                    'customer': {
                        'id': str(customer.id),
                        'email': customer.email,
                        'name': customer.name
                    },
                    'initial_transaction': transaction_info
                }
            }, status=201)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Subscription creation error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def tokenize_card(request):
        """
        Tokenize a payment card for future use
        
        Endpoint: /api/v1/payments/tokenize/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Validate required parameters
            required_fields = ['card', 'customer']
            for field in required_fields:
                if field not in data:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Missing required parameter: {field}'
                    }, status=400)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Find customer
            customer_identifier = data.get('customer')
            try:
                # First try by ID
                customer = Customer.objects.get(
                    id=customer_identifier,
                    merchant=merchant
                )
            except (Customer.DoesNotExist, ValueError):
                # Then try by email
                try:
                    customer = Customer.objects.get(
                        email=customer_identifier,
                        merchant=merchant
                    )
                except Customer.DoesNotExist:
                    # Finally try by external ID
                    try:
                        customer = Customer.objects.get(
                            external_id=customer_identifier,
                            merchant=merchant
                        )
                    except Customer.DoesNotExist:
                        return JsonResponse({
                            'status': 'error',
                            'message': f'Customer not found: {customer_identifier}'
                        }, status=404)
            
            # Get card details
            card = data.get('card', {})
            card_processor = StandalonePaymentProcessor(merchant)
            
            # Validate card details
            validation_result = card_processor._validate_payment_details('credit_card', {'card': card})
            if not validation_result['success']:
                return JsonResponse({
                    'status': 'error',
                    'message': validation_result['error']
                }, status=400)
            
            # Tokenize the card
            payment_details = {'card': card}
            payment_method = card_processor._save_customer_payment_method(customer, payment_details)
            
            if payment_method:
                return JsonResponse({
                    'status': 'success',
                    'message': 'Card tokenized successfully',
                    'data': {
                        'token': payment_method.reference,
                        'last4': payment_method.last4,
                        'card_type': payment_method.card_type,
                        'exp_month': payment_method.exp_month,
                        'exp_year': payment_method.exp_year,
                        'is_default': payment_method.is_default
                    }
                }, status=201)
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Failed to tokenize card'
                }, status=400)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Card tokenization error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
    
    @staticmethod
    @csrf_exempt
    @require_POST
    def process_refund(request, reference):
        """
        Process a refund for a transaction
        
        Endpoint: /api/v1/payments/refund/<reference>/
        Method: POST
        """
        try:
            # Parse request
            data = json.loads(request.body)
            
            # Get API key from header
            api_key = request.headers.get('X-API-Key', '')
            if not api_key:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'API key is required'
                }, status=401)
            
            # Validate merchant using API key
            try:
                merchant = Merchant.objects.get(api_key=api_key)
            except Merchant.DoesNotExist:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Invalid API key'
                }, status=401)
            
            # Get transaction
            try:
                transaction = Transaction.objects.get(
                    reference=reference,
                    merchant=merchant
                )
            except Transaction.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Transaction not found: {reference}'
                }, status=404)
            
            # Process refund
            amount = data.get('amount')  # Can be None for full refund
            reason = data.get('reason', 'Merchant initiated refund')
            
            processor = get_payment_processor(merchant)
            result = processor.process_refund(reference, amount, reason)
            
            return JsonResponse(result, status=200)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON payload'
            }, status=400)
        except Exception as e:
            logger.error(f"Refund processing error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)
            
    @staticmethod
    @csrf_exempt
    def receive_webhook(request):
        """
        Receive and process webhooks from client systems
        
        Endpoint: /api/v1/webhooks/receive/
        Method: POST
        """
        try:
            # Check for signature verification
            signature = request.headers.get('X-Hamsukypay-Signature', '')
            
            # Get raw body data
            body_data = request.body
            
            # Process the webhook data
            result = process_webhook(body_data, signature=signature)
            
            if result.get('success'):
                return JsonResponse({
                    'status': 'success',
                    'message': 'Webhook received and processed'
                }, status=200)
            else:
                logger.warning(f"Failed to process webhook: {result.get('error', 'Unknown error')}")
                return JsonResponse({
                    'status': 'error',
                    'message': result.get('error', 'Failed to process webhook')
                }, status=400)
                
        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            return JsonResponse({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }, status=500)


# View for integration overview page
def integration_overview(request):
    """View for the API integration overview page"""
    return render(request, 'payments/integration_overview.html')

# View for pricing page
def pricing_view(request):
    return render(request, 'payments/pricing.html')

# View for solutions page
def solutions_view(request):
    return render(request, 'payments/solutions.html')

# Custom Admin Views
@staff_member_required
def custom_admin_dashboard(request):
    """
    Main dashboard for the custom admin interface.
    Shows overview stats and recent activities.
    """
    # Get dashboard statistics
    total_transactions = Transaction.objects.count()
    total_merchants = Merchant.objects.count()
    total_customers = Customer.objects.count()
    total_subscriptions = Subscription.objects.count()
    
    # Calculate success rate
    success_count = Transaction.objects.filter(status='success').count()
    success_rate = 0
    if total_transactions > 0:
        success_rate = round((success_count / total_transactions) * 100)
    
    # Calculate total revenue (from successful transactions)
    total_revenue = Transaction.objects.filter(status='success').aggregate(
        total=Sum('amount', default=0)
    )['total'] or 0
    
    # Get recent transactions
    recent_transactions = Transaction.objects.all().order_by('-created_at')[:10]
    
    # Get recent merchants
    recent_merchants = Merchant.objects.all().order_by('-created_at')[:5]
    
    # Get recent customers
    recent_customers = Customer.objects.all().order_by('-created_at')[:5]
    
    # Get revenue data for chart (last 7 days)
    today = timezone.now()
    days_data = []
    labels = []
    
    for i in range(6, -1, -1):
        # Get day date range
        day_date = today - timedelta(days=i)
        day_start = day_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Get revenue for this day
        day_revenue = Transaction.objects.filter(
            status='success',
            created_at__gte=day_start,
            created_at__lte=day_end
        ).aggregate(
            total=Sum('amount', default=0)
        )['total'] or 0
        
        # Add to data arrays
        days_data.append(float(day_revenue))
        labels.append(day_date.strftime('%d %b'))
    
    # Get payment method distribution
    payment_methods = Transaction.objects.filter(
        status='success'
    ).values('payment_method').annotate(
        count=Count('id')
    ).order_by('-count')
    
    total_count = sum(method['count'] for method in payment_methods)
    payment_method_data = []
    payment_method_labels = []
    
    for method in payment_methods[:5]:  # Top 5 payment methods
        if method['payment_method']:
            percentage = round((method['count'] / total_count) * 100) if total_count > 0 else 0
            payment_method_data.append(percentage)
            payment_method_labels.append(method['payment_method'])
    
    # Get recent failed transactions for monitoring
    recent_failed_transactions = Transaction.objects.filter(
        status='failed'
    ).order_by('-created_at')[:5]
    
    # Get flagged transactions that need review
    flagged_transactions = Transaction.objects.filter(
        Q(status='flagged') | Q(compliance_status='review')
    ).order_by('-created_at')[:5]
    
    # Get pending merchant verifications
    pending_verifications = Merchant.objects.filter(
        verification_status='pending'
    ).order_by('-created_at')
    
    # Get system alerts (placeholder for now)
    system_alerts = [
        {
            'id': 1,
            'type': 'warning',
            'message': 'High transaction failure rate detected in the last hour',
            'timestamp': timezone.now() - timedelta(minutes=35),

        },
        {
            'id': 2,
            'type': 'info',
            'message': 'System maintenance scheduled for tonight at 2 AM UTC',
            'timestamp': timezone.now() - datetime.timedelta(hours=2),
        },
    ]
    
    context = {
        'page_title': 'Admin Dashboard',
        'stats': {
            'total_transactions': total_transactions,
            'total_merchants': total_merchants,
            'total_customers': total_customers,
            'total_subscriptions': total_subscriptions,
            'success_rate': f"{success_rate}%",
            'total_revenue': float(total_revenue),
        },
        'recent_transactions': recent_transactions,
        'recent_merchants': recent_merchants,
        'recent_customers': recent_customers,
        'revenue_chart': {
            'labels': labels,
            'data': days_data
        },
        'payment_methods': {
            'labels': payment_method_labels,
            'data': payment_method_data
        },
        'recent_failed_transactions': recent_failed_transactions,
        'flagged_transactions': flagged_transactions,
        'pending_verifications': pending_verifications,
        'system_alerts': system_alerts
    }
    
    return render(request, 'admin_custom/dashboard.html', context)


@staff_member_required
def admin_transactions(request):
    """View to list and filter all transactions"""
    # Get filter parameters
    status = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    search = request.GET.get('search')
    
    # Start with all transactions
    transactions = Transaction.objects.all()
    
    # Apply filters
    if status:
        transactions = transactions.filter(status=status)
    
    if date_from:
        try:
            from_date = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
            transactions = transactions.filter(created_at__date__gte=from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
            transactions = transactions.filter(created_at__date__lte=to_date)
        except ValueError:
            pass
    
    if search:
        transactions = transactions.filter(
            Q(reference__icontains=search) |
            Q(email__icontains=search) |
            Q(customer__name__icontains=search) if search else Q()
        )
    
    # Order by latest first
    transactions = transactions.order_by('-created_at')
    
    # Get summary statistics
    stats = {
        'total_count': transactions.count(),
        'success_count': transactions.filter(status='success').count(),
        'failed_count': transactions.filter(status='failed').count(),
        'pending_count': transactions.filter(status='pending').count(),
    }
    
    # Calculate success rate
    stats['success_rate'] = round((stats['success_count'] / stats['total_count']) * 100) if stats['total_count'] > 0 else 0
    
    # Paginate results
    paginator = Paginator(transactions, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_title': 'Transactions',
        'transactions': page_obj,
        'stats': stats,
        'filters': {
            'status': status,
            'date_from': date_from,
            'date_to': date_to,
            'search': search,
        },
        'statuses': Transaction.STATUS_CHOICES,
    }
    
    return render(request, 'admin_custom/transactions.html', context)


@staff_member_required
def admin_transaction_detail(request, reference):
    """View to show details for a specific transaction"""
    transaction = get_object_or_404(Transaction, reference=reference)
    
    # Get related data
    risk_assessment = None
    try:
        risk_assessment = transaction.risk_assessment
    except:
        pass
    
    compliance_logs = transaction.compliance_logs.all() if hasattr(transaction, 'compliance_logs') else []
    
    context = {
        'page_title': f'Transaction {transaction.reference}',
        'transaction': transaction,
        'risk_assessment': risk_assessment,
        'compliance_logs': compliance_logs,
        'metadata': transaction.get_metadata(),
    }
    
    return render(request, 'admin_custom/transaction_detail.html', context)


@staff_member_required
def admin_merchants(request):
    """View to list and filter all merchants"""
    # Get filter parameters
    status = request.GET.get('verification_status')
    search = request.GET.get('search')
    
    # Start with all merchants
    merchants = Merchant.objects.all()
    
    # Apply filters
    if status:
        merchants = merchants.filter(verification_status=status)
    
    if search:
        merchants = merchants.filter(
            Q(business_name__icontains=search) |
            Q(business_email__icontains=search) |
            Q(user__username__icontains=search) if search else Q()
        )
    
    # Order by latest first
    merchants = merchants.order_by('-created_at')
    
    # Get summary statistics
    stats = {
        'total_count': merchants.count(),
        'verified_count': merchants.filter(verification_status='verified').count(),
        'pending_count': merchants.filter(verification_status='pending').count(),
        'unverified_count': merchants.filter(verification_status='unverified').count(),
    }
    
    # Paginate results
    paginator = Paginator(merchants, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_title': 'Merchants',
        'merchants': page_obj,
        'stats': stats,
        'filters': {
            'status': status,
            'search': search,
        },
        'statuses': Merchant.VERIFICATION_STATUS_CHOICES,
    }
    
    return render(request, 'admin_custom/merchants.html', context)


@staff_member_required
def admin_merchant_detail(request, id):
    """View to show details for a specific merchant"""
    merchant = get_object_or_404(Merchant, id=id)
    
    # Get related data
    transactions = Transaction.objects.filter(merchant=merchant).order_by('-created_at')[:10]
    
    # Calculate merchant statistics
    total_transactions = Transaction.objects.filter(merchant=merchant).count()
    successful_transactions = Transaction.objects.filter(merchant=merchant, status='success').count()
    transaction_volume = Transaction.objects.filter(merchant=merchant, status='success').aggregate(
        total=Sum('amount', default=0)
    )['total'] or 0
    
    # Transaction trend (last 7 days)
    today = timezone.now()
    days_data = []
    labels = []
    
    for i in range(6, -1, -1):
        day_date = today - datetime.timedelta(days=i)
        day_start = day_date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        day_count = Transaction.objects.filter(
            merchant=merchant,
            created_at__gte=day_start,
            created_at__lte=day_end
        ).count()
        
        days_data.append(day_count)
        labels.append(day_date.strftime('%d %b'))
    
    context = {
        'page_title': merchant.business_name,
        'merchant': merchant,
        'transactions': transactions,
        'stats': {
            'total_transactions': total_transactions,
            'successful_transactions': successful_transactions,
            'transaction_volume': float(transaction_volume),
        },
        'trend': {
            'labels': labels,
            'data': days_data
        }
    }
    
    return render(request, 'admin_custom/merchant_detail.html', context)


@staff_member_required
def admin_customers(request):
    """View to list and filter all customers"""
    # Get filter parameters
    search = request.GET.get('search')
    
    # Start with all customers
    customers = Customer.objects.all()
    
    # Apply filters
    if search:
        customers = customers.filter(
            Q(name__icontains=search) |
            Q(email__icontains=search) if search else Q()
        )
    
    # Order by latest first
    customers = customers.order_by('-created_at')
    
    # Get summary statistics
    stats = {
        'total_count': customers.count(),
    }
    
    # Paginate results
    paginator = Paginator(customers, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_title': 'Customers',
        'customers': page_obj,
        'stats': stats,
        'filters': {
            'search': search,
        },
    }
    
    return render(request, 'admin_custom/customers.html', context)


@staff_member_required
def admin_customer_detail(request, id):
    """View to show details for a specific customer"""
    customer = get_object_or_404(Customer, id=id)
    
    # Get related data and statistics (before slicing)
    all_transactions = Transaction.objects.filter(customer=customer)
    total_transactions = all_transactions.count()
    total_spent = all_transactions.filter(status='success').aggregate(
        total=Sum('amount', default=0)
    )['total'] or 0
    
    # Now get the limited transactions for display
    transactions = all_transactions.order_by('-created_at')[:10]
    
    # Get subscriptions
    subscriptions = Subscription.objects.filter(customer=customer).order_by('-created_at')
    
    # Get payment methods
    payment_methods = PaymentMethod.objects.filter(customer=customer)
    
    context = {
        'page_title': customer.name or customer.email,
        'customer': customer,
        'transactions': transactions,
        'subscriptions': subscriptions,
        'payment_methods': payment_methods,
        'stats': {
            'total_transactions': total_transactions,
            'total_spent': float(total_spent),
            'active_subscriptions': subscriptions.filter(status='active').count(),
        },
        'metadata': customer.get_metadata(),
    }
    
    return render(request, 'admin_custom/customer_detail.html', context)


@staff_member_required
def admin_subscriptions(request):
    """View to list and filter all subscriptions"""
    # Get filter parameters
    status = request.GET.get('status')
    search = request.GET.get('search')
    
    # Start with all subscriptions
    subscriptions = Subscription.objects.all()
    
    # Apply filters
    if status:
        subscriptions = subscriptions.filter(status=status)
    
    if search:
        subscriptions = subscriptions.filter(
            Q(reference__icontains=search) |
            Q(customer__email__icontains=search) |
            Q(plan__name__icontains=search) if search else Q()
        )
    
    # Order by latest first
    subscriptions = subscriptions.order_by('-created_at')
    
    # Get summary statistics
    stats = {
        'total_count': subscriptions.count(),
        'active_count': subscriptions.filter(status='active').count(),
        'cancelled_count': subscriptions.filter(status='cancelled').count(),
        'paused_count': subscriptions.filter(status='paused').count(),
    }
    
    # Paginate results
    paginator = Paginator(subscriptions, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_title': 'Subscriptions',
        'subscriptions': page_obj,
        'stats': stats,
        'filters': {
            'status': status,
            'search': search,
        },
        'statuses': Subscription.STATUS_CHOICES,
    }
    
    return render(request, 'admin_custom/subscriptions.html', context)


@staff_member_required
def admin_plans(request):
    """View to list and manage payment plans"""
    # Get filter parameters
    active = request.GET.get('active')
    search = request.GET.get('search')
    
    # Start with all plans
    plans = PaymentPlan.objects.all()
    
    # Apply filters
    if active is not None:
        is_active = active == '1'
        plans = plans.filter(active=is_active)
    
    if search:
        plans = plans.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search) if search else Q()
        )
    
    # Order by name
    plans = plans.order_by('name')
    
    # Get summary statistics
    stats = {
        'total_count': plans.count(),
        'active_count': plans.filter(active=True).count(),
        'inactive_count': plans.filter(active=False).count(),
    }
    
    # Paginate results
    paginator = Paginator(plans, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_title': 'Payment Plans',
        'plans': page_obj,
        'stats': stats,
        'filters': {
            'active': active,
            'search': search,
        },
        'intervals': PaymentPlan.INTERVAL_CHOICES,
    }
    
    return render(request, 'admin_custom/plans.html', context)


@staff_member_required
def admin_analytics(request):
    """View to show analytics dashboard"""
    # Get filter parameters
    period = request.GET.get('period', '30days')
    currency = request.GET.get('currency', 'NGN')
    
    # Calculate date range based on period
    end_date = timezone.now().date()
    
    if period == '7days':
        start_date = end_date - datetime.timedelta(days=7)
    elif period == '30days':
        start_date = end_date - datetime.timedelta(days=30)
    elif period == '90days':
        start_date = end_date - datetime.timedelta(days=90)
    elif period == '1year':
        start_date = end_date - datetime.timedelta(days=365)
    else:
        # Default to 30 days
        start_date = end_date - datetime.timedelta(days=30)
    
    # Get transaction statistics
    transactions = Transaction.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    
    # Filter by currency if specified
    if currency != 'ALL':
        transactions = transactions.filter(currency=currency)
    
    # Calculate summary statistics
    total_transactions = transactions.count()
    successful_transactions = transactions.filter(status='success').count()
    
    # Calculate total revenue by currency
    revenue_by_currency = transactions.filter(status='success').values('currency').annotate(
        total=Sum('amount')
    ).order_by('-total')
    
    # Get transaction count by status
    status_distribution = transactions.values('status').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Get transaction count by day
    transactions_by_day = transactions.extra(
        select={'day': "DATE(created_at)"}
    ).values('day').annotate(
        count=Count('id'),
        success=Count('id', filter=Q(status='success')),
        failed=Count('id', filter=Q(status='failed')),
        revenue=Sum('amount', filter=Q(status='success'))
    ).order_by('day')
    
    # Prepare data for charts
    days = []
    counts = []
    success_counts = []
    failed_counts = []
    revenues = []
    
    for day_data in transactions_by_day:
        # Convert string date to datetime object before formatting
        if day_data['day'] and isinstance(day_data['day'], str):
            try:
                # Parse the date string to a datetime object
                date_obj = datetime.datetime.strptime(day_data['day'], '%Y-%m-%d').date()
                days.append(date_obj.strftime('%d %b'))
            except ValueError:
                # Handle invalid date format
                days.append(str(day_data['day']))  # Just use the string if parsing fails
        elif day_data['day']:
            # If it's already a date object
            days.append(day_data['day'].strftime('%d %b'))
        else:
            # Handle None values
            days.append('N/A')
            
        counts.append(day_data['count'])
        success_counts.append(day_data['success'])
        failed_counts.append(day_data['failed'])
        revenues.append(float(day_data['revenue'] or 0))
    
    # Get payment method distribution
    payment_methods = transactions.exclude(payment_method__isnull=True).values('payment_method').annotate(
        count=Count('id')
    ).order_by('-count')[:5]
    
    # Get available currencies
    available_currencies = Transaction.objects.values_list('currency', flat=True).distinct()
    
    context = {
        'page_title': 'Analytics Dashboard',
        'stats': {
            'total_transactions': total_transactions,
            'successful_transactions': successful_transactions,
            'success_rate': round((successful_transactions / total_transactions) * 100) if total_transactions > 0 else 0,
        },
        'revenue_by_currency': revenue_by_currency,
        'status_distribution': status_distribution,
        'charts': {
            'days': days,
            'counts': counts,
            'success_counts': success_counts,
            'failed_counts': failed_counts,
            'revenues': revenues,
        },
        'payment_methods': payment_methods,
        'filters': {
            'period': period,
            'currency': currency,
        },
        'periods': [
            ('7days', 'Last 7 Days'),
            ('30days', 'Last 30 Days'),
            ('90days', 'Last 90 Days'),
            ('1year', 'Last Year'),
        ],
        'available_currencies': available_currencies,
    }
    
    return render(request, 'admin_custom/analytics.html', context)


@staff_member_required
def admin_settings(request):
    """View to manage admin settings"""
    # Get or create settings from database/cache
    # This is just placeholder for now
    settings_data = {
        'system': {
            'maintenance_mode': False,
            'test_mode': True,
            'webhook_timeout': 15,
            'max_transaction_amount': 10000000,
        },
        'security': {
            'login_attempts': 5,
            'session_timeout': 30,
            'require_2fa': False,
        },
        'notifications': {
            'email_alerts': True,
            'sms_alerts': False,
            'high_value_threshold': 500000,
        },
    }
    
    if request.method == 'POST':
        # Handle settings update logic here
        # This is just placeholder for demonstration
        section = request.POST.get('section')
        if section == 'system':
            settings_data['system']['maintenance_mode'] = request.POST.get('maintenance_mode') == 'on'
            settings_data['system']['test_mode'] = request.POST.get('test_mode') == 'on'
            settings_data['system']['webhook_timeout'] = int(request.POST.get('webhook_timeout', 15))
            settings_data['system']['max_transaction_amount'] = float(request.POST.get('max_transaction_amount', 10000000))
            messages.success(request, 'System settings updated successfully')
        elif section == 'security':
            settings_data['security']['login_attempts'] = int(request.POST.get('login_attempts', 5))
            settings_data['security']['session_timeout'] = int(request.POST.get('session_timeout', 30))
            settings_data['security']['require_2fa'] = request.POST.get('require_2fa') == 'on'
            messages.success(request, 'Security settings updated successfully')
        elif section == 'notifications':
            settings_data['notifications']['email_alerts'] = request.POST.get('email_alerts') == 'on'
            settings_data['notifications']['sms_alerts'] = request.POST.get('sms_alerts') == 'on'
            settings_data['notifications']['high_value_threshold'] = float(request.POST.get('high_value_threshold', 500000))
            messages.success(request, 'Notification settings updated successfully')
    
    context = {
        'page_title': 'Admin Settings',
        'settings': settings_data,
    }
    
    return render(request, 'admin_custom/settings.html', context)


@staff_member_required
def admin_compliance(request):
    """View to manage compliance and risk settings"""
    # Start with all compliance logs
    compliance_logs = ComplianceLog.objects.all().order_by('-created_at')[:50]
    
    # Get flagged transactions
    flagged_transactions = Transaction.objects.filter(
        Q(compliance_status='review') | 
        Q(compliance_status='rejected')
    ).order_by('-created_at')[:10]
    
    # Get high-risk merchants
    high_risk_merchants = Merchant.objects.filter(
        compliance_info__high_risk_category=True
    )
    
    context = {
        'page_title': 'Compliance & Risk',
        'compliance_logs': compliance_logs,
        'flagged_transactions': flagged_transactions,
        'high_risk_merchants': high_risk_merchants,
    }
    
    return render(request, 'admin_custom/compliance.html', context)


def admin_login(request):
    """Custom admin login view"""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('custom_admin_dashboard')
    
    error = None
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        if username and password:
            user = authenticate(request, username=username, password=password)
            
            if user is not None and user.is_staff:
                login(request, user)
                next_url = request.GET.get('next', 'custom_admin_dashboard')
                return redirect(next_url)
            else:
                error = 'Invalid username or password. Please ensure you have administrative access.'
        else:
            error = 'Please enter both username and password'
    
    context = {
        'error': error
    }
    
    return render(request, 'admin_custom/login.html', context)


def admin_logout(request):
    """Handle admin logout"""
    logout(request)
    return redirect('admin_login')


@staff_member_required
def admin_user_management(request):
    """View to manage admin users"""
    # Get all staff users
    staff_users = User.objects.filter(is_staff=True).order_by('-date_joined')
    
    context = {
        'page_title': 'User Management',
        'staff_users': staff_users,
    }
    
    return render(request, 'admin_custom/users.html', context)


@staff_member_required
def admin_transaction_export(request):
    """Export transactions as CSV"""
    from django.http import HttpResponse
    import csv
    
    # Get filter parameters
    status = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    # Start with all transactions
    transactions = Transaction.objects.all()
    
    # Apply filters
    if status:
        transactions = transactions.filter(status=status)
    
    if date_from:
        try:
            from_date = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
            transactions = transactions.filter(created_at__date__gte=from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
            transactions = transactions.filter(created_at__date__lte=to_date)
        except ValueError:
            pass
    
    # Order by latest first
    transactions = transactions.order_by('-created_at')
    
    # Create HTTP response with CSV
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="transactions.csv"'
    
    writer = csv.writer(response)
    writer.writerow([
        'Reference', 'Email', 'Amount', 'Currency', 'Status', 
        'Payment Method', 'Date', 'Merchant'
    ])
    
    for tx in transactions:
        writer.writerow([
            tx.reference,
            tx.email,
            float(tx.amount),
            tx.currency,
            tx.status,
            tx.payment_method or '',
            tx.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            tx.merchant.business_name if tx.merchant else ''
        ])
    
    return response


def currency_converter(request):
    """
    Currency converter page that allows users to convert between different currencies
    using real-time exchange rates.
    """
    from .currency_service import CurrencyService
    
    # Get list of supported currencies
    currencies = CurrencyService.get_supported_currencies()
    
    # Default values
    from_currency = request.GET.get('from', 'USD')
    to_currency = request.GET.get('to', 'NGN')
    amount = request.GET.get('amount', '100')
    converted_amount = None
    formatted_result = None
    conversion_rate = None
    
    # Validate and convert if we have valid inputs
    if request.GET.get('convert') and from_currency and to_currency and amount:
        try:
            # Convert the amount
            decimal_amount = Decimal(amount)
            converted_amount = CurrencyService.convert_amount(
                decimal_amount, from_currency, to_currency
            )
            
            # Format for display
            formatted_result = CurrencyService.format_amount(converted_amount, to_currency)
            
            # Get the conversion rate for display
            rates = CurrencyService.get_exchange_rates(from_currency)
            conversion_rate = rates[to_currency]
            
        except (ValueError, KeyError, InvalidOperation) as e:
            error_message = f"Conversion error: {str(e)}"
            return render(request, 'payments/currency_converter.html', {
                'currencies': currencies,
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount,
                'error': error_message
            })
    
    # Get the latest exchange rates for popular currencies (based on USD)
    popular_currencies = ['EUR', 'GBP', 'NGN', 'ZAR', 'KES', 'GHS']
    usd_rates = CurrencyService.get_exchange_rates('USD')
    popular_rates = {currency: usd_rates.get(currency) for currency in popular_currencies}
    
    return render(request, 'payments/currency_converter.html', {
        'currencies': currencies,
        'from_currency': from_currency,
        'to_currency': to_currency,
        'amount': amount,
        'converted_amount': converted_amount,
        'formatted_result': formatted_result,
        'conversion_rate': conversion_rate,
        'popular_rates': popular_rates
    })


@login_required
def merchant_customers(request):
    """View to display and manage merchant's customers"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        # Get all transactions by this merchant to find customers
        transactions = Transaction.objects.filter(merchant=merchant)
        
        # Get unique customers from transactions
        customer_ids = transactions.values_list('customer', flat=True).distinct()
        customers = Customer.objects.filter(id__in=customer_ids)
        
        # Add additional metrics for each customer
        for customer in customers:
            customer_transactions = transactions.filter(customer=customer)
            customer.transaction_count = customer_transactions.count()
            customer.total_spent = customer_transactions.filter(status='success').aggregate(
                total=Sum('amount', default=0)
            )['total'] or 0
            customer.last_transaction = customer_transactions.order_by('-created_at').first()
        
        return render(request, 'payments/merchant_customers.html', {
            'merchant': merchant,
            'customers': customers
        })
        
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_payment_links(request):
    """View to display and manage merchant's payment links"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        # In a real implementation, you would retrieve payment links from a database
        # For now, we'll create some sample payment links for demonstration
        payment_links = [
            {
                'id': 'pl_1234567890',
                'name': 'Premium Subscription',
                'description': 'Monthly subscription for premium features',
                'amount': 5000.00,
                'currency': 'NGN',
                'url': 'https://pay.hamsukypay.com/pl_1234567890',
                'created_at': timezone.now() - timezone.timedelta(days=5),
                'status': 'active',
                'times_used': 24
            },
            {
                'id': 'pl_0987654321',
                'name': 'Basic Plan',
                'description': 'One-time payment for basic plan',
                'amount': 2500.00,
                'currency': 'NGN',
                'url': 'https://pay.hamsukypay.com/pl_0987654321',
                'created_at': timezone.now() - timezone.timedelta(days=12),
                'status': 'active',
                'times_used': 18
            },
            {
                'id': 'pl_5678901234',
                'name': 'Consultation Fee',
                'description': 'Payment for 1-hour consultation',
                'amount': 10000.00,
                'currency': 'NGN',
                'url': 'https://pay.hamsukypay.com/pl_5678901234',
                'created_at': timezone.now() - timezone.timedelta(days=2),
                'status': 'active',
                'times_used': 5
            }
        ]
        
        return render(request, 'payments/merchant_payment_links.html', {
            'merchant': merchant,
            'payment_links': payment_links
        })
        
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_invoices(request):
    """View to display and manage merchant's invoices"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        # In a real implementation, you would retrieve invoices from a database
        # For now, we'll create some sample invoices for demonstration
        invoices = [
            {
                'id': 'inv_1234567890',
                'customer_name': 'John Doe',
                'customer_email': 'john@example.com',
                'description': 'Website Development Services',
                'amount': 75000.00,
                'currency': 'NGN',
                'due_date': timezone.now() + timezone.timedelta(days=7),
                'created_at': timezone.now() - timezone.timedelta(days=2),
                'status': 'pending',
                'url': 'https://pay.hamsukypay.com/inv_1234567890'
            },
            {
                'id': 'inv_0987654321',
                'customer_name': 'Jane Smith',
                'customer_email': 'jane@example.com',
                'description': 'Monthly Subscription',
                'amount': 5000.00,
                'currency': 'NGN',
                'due_date': timezone.now() + timezone.timedelta(days=14),
                'created_at': timezone.now() - timezone.timedelta(days=1),
                'status': 'pending',
                'url': 'https://pay.hamsukypay.com/inv_0987654321'
            },
            {
                'id': 'inv_5678901234',
                'customer_name': 'Tech Solutions Ltd',
                'customer_email': 'info@techsolutions.com',
                'description': 'Consulting Services - Q2 2025',
                'amount': 250000.00,
                'currency': 'NGN',
                'due_date': timezone.now() - timezone.timedelta(days=5),
                'created_at': timezone.now() - timezone.timedelta(days=20),
                'status': 'paid',
                'url': 'https://pay.hamsukypay.com/inv_5678901234',
                'paid_at': timezone.now() - timezone.timedelta(days=3)
            },
            {
                'id': 'inv_1122334455',
                'customer_name': 'Global Enterprises Inc',
                'customer_email': 'accounts@globalent.com',
                'description': 'Software License - Enterprise',
                'amount': 350000.00,
                'currency': 'NGN',
                'due_date': timezone.now() - timezone.timedelta(days=10),
                'created_at': timezone.now() - timezone.timedelta(days=30),
                'status': 'overdue',
                'url': 'https://pay.hamsukypay.com/inv_1122334455'
            }
        ]
        
        return render(request, 'payments/merchant_invoices.html', {
            'merchant': merchant,
            'invoices': invoices
        })
        
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_payouts(request):
    """View to display and manage merchant's payouts"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        # In a real implementation, you would retrieve payouts from a database
        # For now, we'll create some sample payouts for demonstration
        payouts = [
            {
                'id': 'pyt_1234567890',
                'amount': 125000.00,
                'currency': 'NGN',
                'status': 'completed',
                'bank_name': 'First Bank of Nigeria',
                'account_number': '•••• 4321',
                'created_at': timezone.now() - timezone.timedelta(days=5),
                'settled_at': timezone.now() - timezone.timedelta(days=4)
            },
            {
                'id': 'pyt_0987654321',
                'amount': 78500.00,
                'currency': 'NGN',
                'status': 'completed',
                'bank_name': 'Guaranty Trust Bank',
                'account_number': '•••• 6789',
                'created_at': timezone.now() - timezone.timedelta(days=12),
                'settled_at': timezone.now() - timezone.timedelta(days=11)
            },
            {
                'id': 'pyt_5678901234',
                'amount': 210000.00,
                'currency': 'NGN',
                'status': 'pending',
                'bank_name': 'Zenith Bank',
                'account_number': '•••• 5432',
                'created_at': timezone.now() - timezone.timedelta(days=2),
                'settled_at': None
            }
        ]
        
        return render(request, 'payments/merchant_payouts.html', {
            'merchant': merchant,
            'payouts': payouts
        })
        
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


@login_required
def merchant_webhooks(request):
    """View to manage merchant webhooks"""
    merchant = get_object_or_404(Merchant, user=request.user)
    webhooks = Webhook.objects.filter(merchant=merchant)
    
    # Define available event types
    event_types = [
        {
            'code': 'payment.successful',
            'name': 'Payment Successful',
            'description': 'Triggered when a payment is successfully completed'
        },
        {
            'code': 'payment.failed',
            'name': 'Payment Failed',
            'description': 'Triggered when a payment fails for any reason'
        },
        {
            'code': 'payment.pending',
            'name': 'Payment Pending',
            'description': 'Triggered when a payment is initiated but not yet completed'
        },
        {
            'code': 'subscription.created',
            'name': 'Subscription Created',
            'description': 'Triggered when a new subscription is created'
        },
        {
            'code': 'subscription.cancelled',
            'name': 'Subscription Cancelled',
            'description': 'Triggered when a subscription is cancelled'
        },
        {
            'code': 'subscription.payment_failed',
            'name': 'Subscription Payment Failed',
            'description': 'Triggered when a subscription payment fails'
        },
        {
            'code': 'customer.created',
            'name': 'Customer Created',
            'description': 'Triggered when a new customer is created'
        },
        {
            'code': 'refund.processed',
            'name': 'Refund Processed',
            'description': 'Triggered when a refund is processed'
        },
        {
            'code': 'transfer.successful',
            'name': 'Transfer Successful',
            'description': 'Triggered when a payout transfer is successfully completed'
        },
        {
            'code': 'transfer.failed',
            'name': 'Transfer Failed',
            'description': 'Triggered when a payout transfer fails'
        }
    ]
    
    # Handle webhook creation
    if request.method == 'POST':
        url = request.POST.get('endpointUrl')
        event_type = request.POST.get('eventType')
        description = request.POST.get('description', '')
        
        # Create new webhook
        webhook, message = WebhookService.create_webhook(merchant, url, event_type, description)
        
        if webhook:
            messages.success(request, 'Webhook endpoint created successfully')
            return redirect('payments:merchant_webhooks')
        else:
            messages.error(request, f'Failed to create webhook: {message}')
    
    context = {
        'merchant': merchant,
        'webhooks': webhooks,
        'event_types': event_types
    }
    
    return render(request, 'payments/merchant_webhooks.html', context)


@login_required
def test_webhook(request, webhook_id):
    """View to test a webhook by sending a test event"""
    merchant = get_object_or_404(Merchant, user=request.user)
    webhook = get_object_or_404(Webhook, id=webhook_id, merchant=merchant)
    
    # Send test webhook
    success, status_code, response_text = WebhookService.test_webhook(webhook)
    
    if success:
        messages.success(request, f'Test webhook sent successfully. Response: {status_code}')
    else:
        messages.error(request, f'Test webhook failed. Error: {response_text}')
    
    return redirect('payments:merchant_webhooks')


@login_required
def delete_webhook(request, webhook_id):
    """View to delete a webhook endpoint"""
    merchant = get_object_or_404(Merchant, user=request.user)
    webhook = get_object_or_404(Webhook, id=webhook_id, merchant=merchant)
    
    if request.method == 'POST':
        webhook.delete()
        messages.success(request, 'Webhook deleted successfully')
    
    return redirect('payments:merchant_webhooks')


@login_required
def update_webhook_status(request, webhook_id):
    """View to enable/disable a webhook"""
    merchant = get_object_or_404(Merchant, user=request.user)
    webhook = get_object_or_404(Webhook, id=webhook_id, merchant=merchant)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'enable':
            webhook.status = 'active'
            messages.success(request, 'Webhook activated')
        elif action == 'disable':
            webhook.status = 'inactive'
            messages.success(request, 'Webhook deactivated')
        
        webhook.save()
    
    return redirect('payments:merchant_webhooks')


@login_required
def merchant_support(request):
    """View for merchant support page"""
    merchant = get_object_or_404(Merchant, user=request.user)
    
    if request.method == 'POST':
        # Process support ticket submission
        subject = request.POST.get('subject')
        message = request.POST.get('message')
        ticket_type = request.POST.get('ticket_type')
        priority = request.POST.get('priority', 'medium')
        attachment = request.FILES.get('attachment')
        
        # Create support ticket
        from .models import SupportTicket
        from .support_service import SupportNotificationService
        
        ticket = SupportTicket.objects.create(
            merchant=merchant,
            subject=subject,
            message=message,
            ticket_type=ticket_type,
            priority=priority,
            attachment=attachment
        )
        
        # Send notification to admins and customer service staff
        try:
            SupportNotificationService.notify_new_ticket(ticket)
            messages.success(request, 'Your support request has been submitted successfully. Our team will respond shortly.')
        except Exception as e:
            logger.error(f"Failed to send support ticket notification: {str(e)}")
            messages.success(request, 'Your support request has been submitted successfully, but there was an issue with the notification system. Our team will still respond shortly.')
        
        return redirect('payments:merchant_support')
    
    # Get existing support tickets for this merchant
    from .models import SupportTicket
    support_tickets = SupportTicket.objects.filter(merchant=merchant).order_by('-created_at')
    
    context = {
        'merchant': merchant,
        'page_title': 'Support',
        'support_tickets': support_tickets,
        'ticket_types': SupportTicket.TICKET_TYPE_CHOICES,
        'priority_choices': SupportTicket.PRIORITY_CHOICES
    }
    
    return render(request, 'payments/merchant_support.html', context)


@staff_member_required
def admin_support_tickets(request):
    """Admin view to list and manage support tickets"""
    # Get filter parameters
    status = request.GET.get('status')
    priority = request.GET.get('priority')
    search = request.GET.get('search')
    
    # Start with all tickets
    from .models import SupportTicket
    tickets = SupportTicket.objects.all()
    
    # Apply filters
    if status:
        tickets = tickets.filter(status=status)
    
    if priority:
        tickets = tickets.filter(priority=priority)
    
    if search:
        tickets = tickets.filter(
            models.Q(ticket_id__icontains=search) |
            models.Q(subject__icontains=search) |
            models.Q(merchant__business_name__icontains=search) |
            models.Q(message__icontains=search)
        )
    
    # Order by priority and created date
    tickets = tickets.order_by('status', '-priority', '-created_at')
    
    # Get summary statistics
    stats = {
        'total_tickets': SupportTicket.objects.count(),
        'open_tickets': SupportTicket.objects.filter(status='open').count(),
        'in_progress_tickets': SupportTicket.objects.filter(status='in_progress').count(),
        'resolved_tickets': SupportTicket.objects.filter(status='resolved').count(),
        'closed_tickets': SupportTicket.objects.filter(status='closed').count(),
        'high_priority_tickets': SupportTicket.objects.filter(priority='high').count(),
        'urgent_priority_tickets': SupportTicket.objects.filter(priority='urgent').count()
    }
    
    # Paginate results
    paginator = Paginator(tickets, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get all staff members for assignment
    from django.contrib.auth.models import User
    staff_users = User.objects.filter(is_staff=True)
    
    context = {
        'page_title': 'Support Tickets',
        'tickets': page_obj,
        'stats': stats,
        'filters': {
            'status': status,
            'priority': priority,
            'search': search,
        },
        'status_choices': SupportTicket.TICKET_STATUS_CHOICES,
        'priority_choices': SupportTicket.PRIORITY_CHOICES,
        'staff_users': staff_users
    }
    
    return render(request, 'admin_custom/support_tickets.html', context)


@staff_member_required
def admin_support_ticket_detail(request, ticket_id):
    """Admin view to handle individual support tickets"""
    from .models import SupportTicket, SupportTicketReply, SupportTicketNotification
    from .support_service import SupportNotificationService
    
    ticket = get_object_or_404(SupportTicket, id=ticket_id)
    
    # Mark ticket as read
    if not ticket.is_read:
        ticket.is_read = True
        ticket.save(update_fields=['is_read'])
    
    # Handle status updates
    if request.method == 'POST' and 'update_status' in request.POST:
        previous_status = ticket.status
        new_status = request.POST.get('status')
        
        if new_status != previous_status:
            ticket.status = new_status
            
            # If status is changed to resolved, set resolved_at timestamp
            if new_status == 'resolved' and not ticket.resolved_at:
                ticket.resolved_at = timezone.now()
            
            ticket.save()
            
            # Send notification
            SupportNotificationService.notify_ticket_status_change(ticket, previous_status, request.user)
            
            messages.success(request, f'Ticket status updated to {ticket.get_status_display()}')
    
    # Handle assignment updates
    if request.method == 'POST' and 'assign_ticket' in request.POST:
        assigned_to_id = request.POST.get('assigned_to')
        
        if assigned_to_id:
            from django.contrib.auth.models import User
            try:
                assigned_user = User.objects.get(id=assigned_to_id)
                ticket.assigned_to = assigned_user
                ticket.save()
                messages.success(request, f'Ticket assigned to {assigned_user.get_full_name() or assigned_user.username}')
            except User.DoesNotExist:
                messages.error(request, 'Selected user not found')
        else:
            # Unassign
            ticket.assigned_to = None
            ticket.save()
            messages.success(request, 'Ticket unassigned')
    
    # Handle new replies
    if request.method == 'POST' and 'add_reply' in request.POST:
        reply_message = request.POST.get('reply_message')
        reply_attachment = request.FILES.get('reply_attachment')
        
        if reply_message:
            # Create the reply
            reply = SupportTicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                is_admin=True,  # This is an admin reply
                message=reply_message,
                attachment=reply_attachment
            )
            
            # If ticket was closed, reopen it
            if ticket.status == 'closed':
                ticket.status = 'in_progress'
                ticket.save()
            
            # Send notification
            SupportNotificationService.notify_ticket_reply(reply)
            
            messages.success(request, 'Your reply has been sent')
    
    # Get all replies for this ticket
    replies = SupportTicketReply.objects.filter(ticket=ticket).order_by('created_at')
    
    # Get all staff members for assignment
    from django.contrib.auth.models import User
    staff_users = User.objects.filter(is_staff=True)
    
    context = {
        'page_title': f'Ticket {ticket.ticket_id}',
        'ticket': ticket,
        'replies': replies,
        'staff_users': staff_users,
    }
    
    return render(request, 'admin_custom/support_ticket_detail.html', context)


@staff_member_required
def admin_support_dashboard(request):
    """Admin view for support dashboard with overview and analytics"""
    from .models import SupportTicket
    from django.db.models import Count, Avg, F, ExpressionWrapper, fields, Q
    
    # Get overall statistics
    total_tickets = SupportTicket.objects.count()
    open_tickets = SupportTicket.objects.filter(status='open').count()
    in_progress_tickets = SupportTicket.objects.filter(status='in_progress').count()
    resolved_tickets = SupportTicket.objects.filter(status='resolved').count()
    closed_tickets = SupportTicket.objects.filter(status='closed').count()
    
    # Today's tickets
    today = timezone.now().date()
    today_tickets = SupportTicket.objects.filter(created_at__date=today).count()
    today_resolved = SupportTicket.objects.filter(resolved_at__date=today).count()
    
    # Calculate response time metrics
    resolution_time = None
    try:
        # Add a field for resolution time in hours
        resolution_expr = ExpressionWrapper(
            F('resolved_at') - F('created_at'),
            output_field=fields.DurationField()
        )
        
        resolved_tickets_with_time = SupportTicket.objects.filter(
            status__in=['resolved', 'closed'],
            resolved_at__isnull=False
        ).annotate(resolution_time=resolution_expr)
        
        # Calculate average resolution time in hours
        from django.db.models import Avg
        from datetime import timedelta
        
        avg_resolution = resolved_tickets_with_time.aggregate(
            avg=Avg('resolution_time')
        )['avg']
        
        if avg_resolution:
            avg_hours = avg_resolution.total_seconds() / 3600
            resolution_time = round(avg_hours, 1)
    except Exception as e:
        print(f"Error calculating resolution time: {str(e)}")
    
    # Get high priority tickets
    high_priority_tickets = SupportTicket.objects.filter(
        Q(priority='high') | Q(priority='urgent'),
        status__in=['open', 'in_progress']
    ).order_by('-priority', 'created_at')[:5]
    
    # Get unassigned tickets
    unassigned_tickets = SupportTicket.objects.filter(
        assigned_to__isnull=True,
        status__in=['open', 'in_progress']
    ).order_by('-created_at')[:5]
    
    # Get ticket distribution by type
    ticket_types = SupportTicket.objects.values('ticket_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Get oldest open tickets
    oldest_tickets = SupportTicket.objects.filter(
        status__in=['open', 'in_progress']
    ).order_by('created_at')[:5]
    
    context = {
        'page_title': 'Support Dashboard',
        'stats': {
            'total_tickets': total_tickets,
            'open_tickets': open_tickets,
            'in_progress_tickets': in_progress_tickets,
            'resolved_tickets': resolved_tickets,
            'closed_tickets': closed_tickets,
            'today_tickets': today_tickets,
            'today_resolved': today_resolved,
            'resolution_time': resolution_time,
            'open_percentage': round((open_tickets / total_tickets) * 100) if total_tickets > 0 else 0,
            'resolved_percentage': round((resolved_tickets / total_tickets) * 100) if total_tickets > 0 else 0
        },
        'high_priority_tickets': high_priority_tickets,
        'unassigned_tickets': unassigned_tickets,
        'ticket_types': ticket_types,
        'oldest_tickets': oldest_tickets
    }
    
    return render(request, 'admin_custom/support_dashboard.html', context)


@login_required
@staff_member_required
def admin_support_dashboard(request):
    """Admin dashboard view for support ticket analytics"""
    from django.db.models import Count, Q
    from .models import SupportTicket
    
    # Get ticket statistics
    ticket_stats = {
        'total_tickets': SupportTicket.objects.count(),
        'open_tickets': SupportTicket.objects.filter(status='open').count(),
        'in_progress_tickets': SupportTicket.objects.filter(status='in_progress').count(),
        'resolved_tickets': SupportTicket.objects.filter(status='resolved').count(),
        'closed_tickets': SupportTicket.objects.filter(status='closed').count(),
        'urgent_priority_tickets': SupportTicket.objects.filter(priority='urgent').exclude(status__in=['resolved', 'closed']).count(),
        'high_priority_tickets': SupportTicket.objects.filter(priority='high').exclude(status__in=['resolved', 'closed']).count(),
    }
    
    # Get ticket types distribution
    ticket_types = SupportTicket.objects.values('ticket_type').annotate(count=Count('id')).order_by('-count')
    
    # Get recent tickets
    recent_tickets = SupportTicket.objects.all().order_by('-created_at')[:10]
    
    # Get high priority tickets
    high_priority_tickets = SupportTicket.objects.filter(
        Q(priority='urgent') | Q(priority='high')
    ).exclude(
        status__in=['resolved', 'closed']
    ).order_by('-created_at')[:10]
    
    # Get tickets awaiting response (open tickets with no reply)
    from django.db.models import OuterRef, Exists
    from .models import SupportTicketReply
    
    has_reply = SupportTicketReply.objects.filter(ticket=OuterRef('pk')).values('id')
    
    awaiting_response = SupportTicket.objects.filter(
        status__in=['open', 'in_progress']
    ).annotate(
        has_reply=Exists(has_reply)
    ).filter(
        has_reply=False
    ).order_by('-created_at')[:10]
    
    context = {
        'page_title': 'Support Dashboard',
        'stats': ticket_stats,
        'ticket_types': ticket_types,
        'recent_tickets': recent_tickets,
        'high_priority_tickets': high_priority_tickets,
        'awaiting_response': awaiting_response,
    }
    
    return render(request, 'admin_custom/support_dashboard.html', context)


@login_required
@staff_member_required
def admin_support_tickets(request):
    """View for admin to list and filter all support tickets"""
    from django.core.paginator import Paginator
    from django.db.models import Q
    from .models import SupportTicket
    
    # Get filters from request
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    search_query = request.GET.get('search', '')
    
    # Base queryset
    tickets = SupportTicket.objects.all()
    
    # Apply filters
    if status_filter:
        tickets = tickets.filter(status=status_filter)
        
    if priority_filter:
        tickets = tickets.filter(priority=priority_filter)
        
    if search_query:
        tickets = tickets.filter(
            Q(ticket_id__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(message__icontains=search_query) |
            Q(merchant__business_name__icontains=search_query)
        )
    
    # Order by priority (highest first) and then created date (newest first)
    tickets = tickets.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(tickets, 20)  # 20 tickets per page
    page_number = request.GET.get('page', 1)
    tickets_page = paginator.get_page(page_number)
    
    # Get all staff users for ticket assignment
    from django.contrib.auth.models import User
    staff_users = User.objects.filter(is_staff=True, is_active=True)
    
    # Get ticket stats for the cards
    ticket_stats = {
        'total_tickets': SupportTicket.objects.count(),
        'open_tickets': SupportTicket.objects.filter(status='open').count(),
        'in_progress_tickets': SupportTicket.objects.filter(status='in_progress').count(),
        'resolved_tickets': SupportTicket.objects.filter(status='resolved').count(),
        'urgent_priority_tickets': SupportTicket.objects.filter(priority='urgent').exclude(status__in=['resolved', 'closed']).count(),
        'high_priority_tickets': SupportTicket.objects.filter(priority='high').exclude(status__in=['resolved', 'closed']).count(),
    }
    
    context = {
        'page_title': 'Support Tickets',
        'tickets': tickets_page,
        'staff_users': staff_users,
        'status_choices': SupportTicket.TICKET_STATUS_CHOICES,
        'priority_choices': SupportTicket.PRIORITY_CHOICES,
        'ticket_type_choices': SupportTicket.TICKET_TYPE_CHOICES,
        'filters': {
            'status': status_filter,
            'priority': priority_filter,
            'search': search_query,
        },
        'stats': ticket_stats,
    }
    
    return render(request, 'admin_custom/support_tickets.html', context)


@login_required
@staff_member_required
def admin_support_ticket_detail(request, ticket_id):
    """View for admin to view and manage a specific support ticket"""
    from django.contrib import messages
    from django.http import HttpResponseRedirect
    from .models import SupportTicket, SupportTicketReply
    from .support_service import SupportNotificationService
    
    ticket = get_object_or_404(SupportTicket, id=ticket_id)
    
    # Get current path for redirect
    current_path = request.path
    
    # Mark ticket as read if it wasn't
    if not ticket.is_read:
        ticket.is_read = True
        ticket.save(update_fields=['is_read'])
    
    # Handle ticket status update
    if request.method == 'POST' and 'update_status' in request.POST:
        previous_status = ticket.status
        new_status = request.POST.get('status')
        
        if new_status in dict(SupportTicket.TICKET_STATUS_CHOICES):
            ticket.status = new_status
            
            # If resolving, set resolved time
            if new_status == 'resolved' and previous_status != 'resolved':
                ticket.resolved_at = timezone.now()
            
            ticket.save()
            
            # Send notification about status change
            try:
                SupportNotificationService.notify_status_change(ticket, previous_status, new_status)
            except Exception as e:
                messages.warning(request, f"Status updated but notification failed: {str(e)}")
        
        # Redirect to the same page using the current path
        return HttpResponseRedirect(current_path)
    
    # Handle ticket assignment
    elif request.method == 'POST' and 'assign_ticket' in request.POST:
        from django.contrib.auth.models import User
        
        user_id = request.POST.get('assigned_to', '')
        
        if user_id:
            try:
                assigned_user = User.objects.get(id=user_id)
                ticket.assigned_to = assigned_user
                ticket.save()
                messages.success(request, f"Ticket assigned to {assigned_user.username}")
            except User.DoesNotExist:
                messages.error(request, "Invalid user selected")
        else:
            # Unassign
            ticket.assigned_to = None
            ticket.save()
            messages.success(request, "Ticket unassigned")
        
        # Redirect to the same page using the current path
        return HttpResponseRedirect(current_path)
    
    # Handle reply submission
    elif request.method == 'POST' and 'add_reply' in request.POST:
        message = request.POST.get('reply_message', '').strip()
        attachment = request.FILES.get('reply_attachment')
        
        if message:
            # Create the reply
            reply = SupportTicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                message=message,
                attachment=attachment,
                is_admin=True
            )
            
            # Update ticket status to in-progress if it's open
            if ticket.status == 'open':
                ticket.status = 'in_progress'
                ticket.save()
            
            # Send notification about the reply
            try:
                SupportNotificationService.notify_merchant_of_reply(reply)
                messages.success(request, "Reply added and notification sent successfully")
            except Exception as e:
                messages.success(request, f"Reply added but notification failed: {str(e)}")
            
            # Redirect to the same page using the current path
            return HttpResponseRedirect(current_path)
        else:
            messages.error(request, "Response message cannot be empty.")
        
        # Redirect to the same page using the current path
        return HttpResponseRedirect(current_path)
    
    # Get ticket replies
    replies = SupportTicketReply.objects.filter(ticket=ticket).order_by('created_at')
    
    # Get all staff users for ticket assignment
    from django.contrib.auth.models import User
    staff_users = User.objects.filter(is_staff=True, is_active=True)
    
    context = {
        'page_title': f'Ticket {ticket.ticket_id}',
        'ticket': ticket,
        'replies': replies,
        'staff_users': staff_users,
        'status_choices': SupportTicket.TICKET_STATUS_CHOICES,
    }
    
    return render(request, 'admin_custom/support_ticket_detail.html', context)


@login_required
def merchant_support_detail(request, ticket_id):
    """View for merchant to view and respond to a specific support ticket"""
    from django.contrib import messages
    from .models import SupportTicket, SupportTicketReply
    from .support_service import SupportNotificationService
    
    merchant = get_object_or_404(Merchant, user=request.user)
    ticket = get_object_or_404(SupportTicket, id=ticket_id, merchant=merchant)
    
    # Handle reply submission
    if request.method == 'POST' and 'add_reply' in request.POST:
        message = request.POST.get('reply_message', '').strip()
        attachment = request.FILES.get('reply_attachment')
        
        if message:
            # Create the reply
            reply = SupportTicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                message=message,
                attachment=attachment,
                is_admin=False
            )
            
            # Update ticket status to open if it was resolved or closed
            if ticket.status in ['resolved', 'closed']:
                ticket.status = 'open'
                ticket.save()
            
            # Send notification about the reply
            try:
                SupportNotificationService.notify_ticket_reply(reply)
                messages.success(request, "Your response has been added and our team has been notified")
            except Exception as e:
                logger.error(f"Failed to send reply notification: {str(e)}")
                messages.warning(request, "Your response has been added, but there was an issue with notifications.")
        else:
            messages.error(request, "Response message cannot be empty.")
            
        return redirect('merchant_support_detail', ticket_id=ticket.id)
    
    # Get ticket replies
    replies = SupportTicketReply.objects.filter(ticket=ticket).order_by('created_at')
    
    context = {
        'merchant': merchant,
        'page_title': f'Ticket {ticket.ticket_id}',
        'ticket': ticket,
        'replies': replies,
    }
    
    return render(request, 'payments/merchant_support_detail.html', context)


# Support Ticket Views for Admin and Customer Service
@staff_member_required
def admin_support_tickets(request):
    """View to list all support tickets for admin staff"""
    # Get filter parameters
    status = request.GET.get('status', '')
    priority = request.GET.get('priority', '')
    ticket_type = request.GET.get('type', '')
    
    # Start with all tickets
    tickets = SupportTicket.objects.all()
    
    # Apply filters if provided
    if status:
        tickets = tickets.filter(status=status)
    if priority:
        tickets = tickets.filter(priority=priority)
    if ticket_type:
        tickets = tickets.filter(ticket_type=ticket_type)
    
    # Order by most recent first
    tickets = tickets.order_by('-created_at')
    
    # Paginate results
    paginator = Paginator(tickets, 20)  # 20 tickets per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'admin_custom/support_tickets.html', {
        'tickets': page_obj,
        'filters': {
            'status': status,
            'priority': priority,
            'type': ticket_type
        },
        'statuses': SupportTicket.TICKET_STATUS_CHOICES,
        'priorities': SupportTicket.PRIORITY_CHOICES,
        'types': SupportTicket.TICKET_TYPE_CHOICES
    })

@staff_member_required
def admin_support_ticket_detail(request, ticket_id):
    """View to show and handle a specific support ticket for admin staff"""
    ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)
    
    # Mark ticket as read when admin views it
    if not ticket.is_read:
        ticket.is_read = True
        ticket.save()
    
    # Handle new reply submission
    if request.method == 'POST':
        message = request.POST.get('message', '').strip()
        attachment = request.FILES.get('attachment')
        
        if message:
            # Create reply
            reply = SupportTicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                is_admin=True,
                message=message,
                attachment=attachment
            )
            
            # Update ticket status if requested
            new_status = request.POST.get('update_status')
            if new_status and new_status in dict(SupportTicket.TICKET_STATUS_CHOICES):
                ticket.status = new_status
                if new_status == 'resolved':
                    ticket.resolved_at = timezone.now()
            
            assignee_id = request.POST.get('assign_to')
            if assignee_id:
                if assignee_id:
                    try:
                        assignee = User.objects.get(id=assignee_id)
                        ticket.assigned_to = assignee
                        ticket.save()
                        
                        # Create notification for assignment
                        SupportTicketNotification.objects.create(
                            ticket=ticket,
                            notification_type='assignment',
                            recipient=assignee
                        )
                    except User.DoesNotExist:
                        pass
            
            # Add success message
            messages.success(request, 'Reply added successfully')
            
            # Send notification to merchant about new reply
            try:
                from .email_service import EmailService
                EmailService.send_ticket_reply_notification(ticket, reply)
            except Exception as e:
                logger.error(f"Failed to send ticket reply notification: {str(e)}")
            
            # Create notification record
            SupportTicketNotification.objects.create(
                ticket=ticket,
                notification_type='ticket_reply',
                recipient_email=ticket.merchant.business_email
            )
            
            # Redirect to prevent form resubmission
            return redirect('admin_support_ticket_detail', ticket_id=ticket_id)
    
    # Get all replies for this ticket
    replies = ticket.replies.all().order_by('created_at')
    
    # Get all admin users who can be assigned
    admin_users = User.objects.filter(is_staff=True)
    
    return render(request, 'admin_custom/support_ticket_detail.html', {
        'ticket': ticket,
        'replies': replies,
        'admin_users': admin_users,
        'statuses': SupportTicket.TICKET_STATUS_CHOICES
    })

@staff_member_required
def admin_update_ticket_status(request, ticket_id):
    """API view to update ticket status"""
    if request.method == 'POST':
        ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)
        new_status = request.POST.get('status')
        
        if new_status and new_status in dict(SupportTicket.TICKET_STATUS_CHOICES):
            old_status = ticket.status
            ticket.status = new_status
            
            # Set resolved_at timestamp if status changed to resolved
            if new_status == 'resolved' and old_status != 'resolved':
                ticket.resolved_at = timezone.now()
            
            ticket.save()
            
            # Create notification for status change
            SupportTicketNotification.objects.create(
                ticket=ticket,
                notification_type='status_change',
                recipient_email=ticket.merchant.business_email
            )
            
            # Send email notification
            try:
                from .email_service import EmailService
                EmailService.send_ticket_status_update(ticket)
            except Exception as e:
                logger.error(f"Failed to send ticket status update notification: {str(e)}")
            
            return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

@staff_member_required
def admin_assign_ticket(request, ticket_id):
    """API view to assign ticket to staff member"""
    if request.method == 'POST':
        ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)
        assignee_id = request.POST.get('assignee_id')
        
        try:
            if assignee_id:
                assignee = User.objects.get(id=assignee_id)
                ticket.assigned_to = assignee
            else:
                # Unassign
                ticket.assigned_to = None
                
            ticket.save()
            
            # Create notification for assignment
            if assignee_id:
                SupportTicketNotification.objects.create(
                    ticket=ticket,
                    notification_type='assignment',
                    recipient=ticket.assigned_to
                )
            
            return JsonResponse({'success': True})
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid user'}, status=400)
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

# Merchant support ticket views
@login_required
def merchant_support_tickets(request):
    """View for merchants to see their support tickets"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        # Get tickets for this merchant
        tickets = SupportTicket.objects.filter(merchant=merchant).order_by('-created_at')
        
        # Paginate results
        paginator = Paginator(tickets, 10)  # 10 tickets per page
        page_number = request.GET.get('page', 1)
        tickets_page = paginator.get_page(page_number)
        
        return render(request, 'payments/merchant_support_tickets.html', {
            'merchant': merchant,
            'tickets': tickets_page
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')

@login_required
def merchant_support_ticket_detail(request, ticket_id):
    """View for merchants to view and respond to a specific support ticket"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id, merchant=merchant)
        
        # Handle new reply submission
        if request.method == 'POST':
            message = request.POST.get('message', '').strip()
            attachment = request.FILES.get('attachment')
            
            if message:
                # Create reply
                reply = SupportTicketReply.objects.create(
                    ticket=ticket,
                    user=request.user,
                    is_admin=False,
                    message=message,
                    attachment=attachment
                )
                
                # Update ticket status if it was resolved or closed
                if ticket.status in ['resolved', 'closed']:
                    ticket.status = 'open'
                    ticket.save()
                
                # Add success message
                messages.success(request, 'Reply added successfully')
                
                # Send notification to admin about new reply
                try:
                    from .email_service import EmailService
                    EmailService.send_admin_ticket_reply_notification(ticket, reply)
                except Exception as e:
                    logger.error(f"Failed to send admin ticket reply notification: {str(e)}")
                
                # Create notification record for admins
                if ticket.assigned_to:
                    SupportTicketNotification.objects.create(
                        ticket=ticket,
                        notification_type='ticket_reply',
                        recipient=ticket.assigned_to
                    )
                else:
                    # Notify all admin users if no specific assignee
                    admin_emails = User.objects.filter(is_staff=True).values_list('email', flat=True)
                    for email in admin_emails:
                        SupportTicketNotification.objects.create(
                            ticket=ticket,
                            notification_type='ticket_reply',
                            recipient_email=email
                        )
                
                # Redirect to prevent form resubmission
                return redirect('payments:merchant_support_ticket_detail', ticket_id=ticket_id)
        
        # Get all replies for this ticket
        replies = ticket.replies.all().order_by('created_at')
        
        return render(request, 'payments/merchant_support_ticket_detail.html', {
            'merchant': merchant,
            'ticket': ticket,
            'replies': replies
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')

@login_required
def merchant_create_support_ticket(request):
    """View for merchants to create a new support ticket"""
    try:
        merchant = Merchant.objects.get(user=request.user)
        
        if request.method == 'POST':
            subject = request.POST.get('subject', '').strip()
            message = request.POST.get('message', '').strip()
            ticket_type = request.POST.get('ticket_type')
            priority = request.POST.get('priority', 'medium')
            attachment = request.POST.get('attachment')
            
            # Validate inputs
            if not subject or not message or not ticket_type:
                messages.error(request, 'Please fill in all required fields')
                return render(request, 'payments/merchant_create_support_ticket.html', {
                    'merchant': merchant,
                    'ticket_types': SupportTicket.TICKET_TYPE_CHOICES,
                    'priorities': SupportTicket.PRIORITY_CHOICES
                })
            
            # Create the ticket
            ticket = SupportTicket.objects.create(
                merchant=merchant,
                subject=subject,
                message=message,
                ticket_type=ticket_type,
                priority=priority,
                status='open',
                attachment=attachment
            )
            
            # Send notification to admin about new ticket
            try:
                from .email_service import EmailService
                EmailService.send_new_ticket_notification(ticket)
            except Exception as e:
                logger.error(f"Failed to send new ticket notification: {str(e)}")
            
            # Create notification record for admins
            admin_emails = User.objects.filter(is_staff=True).values_list('email', flat=True)
            for email in admin_emails:
                SupportTicketNotification.objects.create(
                    ticket=ticket,
                    notification_type='new_ticket',
                    recipient_email=email
                )
            
            messages.success(request, f'Support ticket #{ticket.ticket_id} created successfully')
            return redirect('payments:merchant_support_tickets')
        
        return render(request, 'payments/merchant_create_support_ticket.html', {
            'merchant': merchant,
            'ticket_types': SupportTicket.TICKET_TYPE_CHOICES,
            'priorities': SupportTicket.PRIORITY_CHOICES
        })
    except Merchant.DoesNotExist:
        return redirect('payments:merchant_register')


# Support Ticket Views
@login_required
def support_tickets_list(request):
    """View for listing all support tickets for a merchant"""
    merchant = get_object_or_404(Merchant, user=request.user)
    tickets = SupportTicket.objects.filter(merchant=merchant).order_by('-created_at')
    
    # Filter by status if provided
    status_filter = request.GET.get('status')
    if status_filter and status_filter != 'all':
        tickets = tickets.filter(status=status_filter)
    
    # Pagination
    paginator = Paginator(tickets, 10)  # 10 tickets per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get ticket counts by status
    open_count = tickets.filter(status='open').count()
    in_progress_count = tickets.filter(status='in_progress').count()
    resolved_count = tickets.filter(status='resolved').count()
    closed_count = tickets.filter(status='closed').count()
    
    context = {
        'tickets': page_obj,
        'total_tickets': tickets.count(),
        'open_count': open_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
        'closed_count': closed_count,
        'current_status': status_filter or 'all'
    }
    
    return render(request, 'payments/merchant_support_tickets.html', context)


@login_required
def create_support_ticket(request):
    """View for creating a new support ticket"""
    merchant = get_object_or_404(Merchant, user=request.user)
    
    if request.method == 'POST':
        subject = request.POST.get('subject')
        message = request.POST.get('message')
        ticket_type = request.POST.get('ticket_type')
        priority = request.POST.get('priority', 'medium')
        
        if not all([subject, message, ticket_type]):
            messages.error(request, "Please fill all required fields")
            return redirect('payments:create_support_ticket')
        
        # Create the support ticket
        ticket = SupportTicket.objects.create(
            merchant=merchant,
            subject=subject,
            message=message,
            ticket_type=ticket_type,
            priority=priority
        )
        
        # Handle attachment if provided
        attachment = request.FILES.get('attachment')
        if attachment:
            ticket.attachment = attachment
            ticket.save()
        
        messages.success(request, f"Your support ticket has been submitted with ID: {ticket.ticket_id}")
        return redirect('payments:support_ticket_detail', ticket.ticket_id)
    
    return render(request, 'payments/create_support_ticket.html', {
        'ticket_types': SupportTicket.TICKET_TYPE_CHOICES,
        'priorities': SupportTicket.PRIORITY_CHOICES
    })


@login_required
def support_ticket_detail(request, ticket_id):
    """View for displaying a single support ticket and its replies"""
    merchant = get_object_or_404(Merchant, user=request.user)
    ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id, merchant=merchant)
    
    # Mark ticket as read if it wasn't already
    if not ticket.is_read:
        ticket.is_read = True
        ticket.save()
    
    # Get all replies for this ticket
    replies = ticket.replies.all().order_by('created_at')
    
    if request.method == 'POST':
        # Handle new reply
        message = request.POST.get('message')
        if message:
            reply = SupportTicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                is_admin=False,  # Merchant reply
                message=message
            )
            
            # Handle attachment if provided
            attachment = request.FILES.get('attachment')
            if attachment:
                reply.attachment = attachment
                reply.save()
            
            # Update ticket status to in_progress if it was resolved/closed 
            # and a merchant responds
            if ticket.status in ['resolved', 'closed']:
                ticket.status = 'in_progress'
                ticket.save()
            
            # Update ticket's last update time
            ticket.save()  # This will update the auto_now field
            
            messages.success(request, "Your reply has been added.")
            return redirect('payments:support_ticket_detail', ticket_id=ticket_id)
    
    context = {
        'ticket': ticket,
        'replies': replies
    }
    
    return render(request, 'payments/support_ticket_detail.html', context)


@login_required
def update_ticket_status(request, ticket_id):
    """View for updating the status of a support ticket"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
    
    merchant = get_object_or_404(Merchant, user=request.user)
    ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id, merchant=merchant)
    
    new_status = request.POST.get('status')
    if new_status not in [status[0] for status in SupportTicket.TICKET_STATUS_CHOICES]:
        return JsonResponse({'status': 'error', 'message': 'Invalid status'}, status=400)
    
    # Update the ticket status
    old_status = ticket.status
    ticket.status = new_status
    
    # Set resolved_at timestamp if status changed to resolved
    if new_status == 'resolved' and old_status != 'resolved':
        ticket.resolved_at = timezone.now()
    
    ticket.save()
    
    return JsonResponse({'status': 'success', 'message': f'Ticket status updated to {new_status}'})


# Admin Support Ticket Views
@staff_member_required
def admin_support_tickets(request):
    """Admin view for listing all support tickets"""
    tickets = SupportTicket.objects.all().order_by('-created_at')
    
    # Filter by status if provided
    status_filter = request.GET.get('status')
    if status_filter and status_filter != 'all':
        tickets = tickets.filter(status=status_filter)
    
    # Filter by merchant if provided
    merchant_id = request.GET.get('merchant')
    if (merchant_id):
        tickets = tickets.filter(merchant_id=merchant_id)
    
    # Pagination
    paginator = Paginator(tickets, 20)  # 20 tickets per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get ticket counts by status
    open_count = tickets.filter(status='open').count()
    in_progress_count = tickets.filter(status='in_progress').count()
    resolved_count = tickets.filter(status='resolved').count()
    closed_count = tickets.filter(status='closed').count()
    
    # Get all merchants for filtering
    merchants = Merchant.objects.all()
    
    context = {
        'tickets': page_obj,
        'total_tickets': tickets.count(),
        'open_count': open_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
        'closed_count': closed_count,
        'current_status': status_filter or 'all',
        'merchants': merchants,
        'selected_merchant': merchant_id
    }
    
    return render(request, 'admin_custom/support_tickets.html', context)


@staff_member_required
def admin_ticket_detail(request, ticket_id):
    """Admin view for handling a support ticket"""
    ticket = get_object_or_404(SupportTicket, ticket_id=ticket_id)
    
    # Get all replies for this ticket
    replies = ticket.replies.all().order_by('created_at')
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'reply':
            # Handle new admin reply
            message = request.POST.get('message')
            if message:
                reply = SupportTicketReply.objects.create(
                    ticket=ticket,
                    user=request.user,
                    is_admin=True,  # Admin reply
                    message=message
                )
                
                # Handle attachment if provided
                attachment = request.FILES.get('attachment')
                if attachment:
                    reply.attachment = attachment
                    reply.save()
                
                # Update ticket's last update time
                ticket.save()
                
                messages.success(request, "Reply added successfully.")
                return redirect('payments:admin_ticket_detail', ticket_id=ticket_id)
        
        elif action == 'update_status':
            # Handle status update
            new_status = request.POST.get('status')
            if new_status in [status[0] for status in SupportTicket.TICKET_STATUS_CHOICES]:
                old_status = ticket.status
                ticket.status = new_status
                # Set resolved_at timestamp if status changed to resolved
                if new_status == 'resolved' and old_status != 'resolved':
                    ticket.resolved_at = timezone.now()
                ticket.save()
                messages.success(request, f"Ticket status updated to {new_status}.")
                return redirect('payments:admin_ticket_detail', ticket_id=ticket_id)
        
        elif action == 'assign':
            # Handle ticket assignment
            user_id = request.POST.get('assigned_to')
            if user_id:
                try:
                    assigned_user = User.objects.get(id=user_id)
                    ticket.assigned_to = assigned_user
                    ticket.save()
                    messages.success(request, f"Ticket assigned to {assigned_user.username}.")
                except User.DoesNotExist:
                    messages.error(request, "Invalid user selected for assignment.")
            else:
                ticket.assigned_to = None
                ticket.save()
                messages.success(request, "Ticket unassigned.")
            return redirect('payments:admin_ticket_detail', ticket_id=ticket_id)

    
    # Get admin users for assignment
    admin_users = User.objects.filter(is_staff=True)
    
    context = {
        'ticket': ticket,
        'replies': replies,
        'admin_users': admin_users
    }
    
    return render(request, 'admin_custom/support_ticket_detail.html', context)


# Support ticket API views
class SupportTicketsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get all tickets for the authenticated merchant"""
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Merchant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        tickets = SupportTicket.objects.filter(merchant=merchant)
        
        # Apply filters
        ticket_status = request.query_params.get('status')
        if ticket_status:
            tickets = tickets.filter(status=ticket_status)
        
        # Implement basic pagination
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        start = (page - 1) * page_size
        end = start + page_size
        
        tickets_data = []
        for ticket in tickets[start:end]:
            tickets_data.append({
                'ticket_id': ticket.ticket_id,
                'subject': ticket.subject,
                'status': ticket.status,
                'priority': ticket.priority,
                'type': ticket.ticket_type,
                'created_at': ticket.created_at.isoformat(),
                'updated_at': ticket.updated_at.isoformat(),
                'replied': ticket.replies.exists(),
                'unread': not ticket.is_read
            })
        
        return Response({
            'status': 'success',
            'data': {
                'tickets': tickets_data,
                'total': tickets.count(),
                'page': page,
                'page_size': page_size
            }
        })
    
    def post(self, request):
        """Create a new support ticket"""
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Merchant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Validate request data
        required_fields = ['subject', 'message', 'ticket_type']
        for field in required_fields:
            if not request.data.get(field):
                return Response({
                    'status': 'error',
                    'message': f'Missing required field: {field}'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create ticket
        ticket = SupportTicket.objects.create(
            merchant=merchant,
            subject=request.data.get('subject'),
            message=request.data.get('message'),
            ticket_type=request.data.get('ticket_type'),
            priority=request.data.get('priority', 'medium')
        )
        
        return Response({
            'status': 'success',
            'message': 'Support ticket created successfully',
            'data': {
                'ticket_id': ticket.ticket_id,
                'subject': ticket.subject,
                'status': ticket.status,
                'created_at': ticket.created_at.isoformat()
            }
        }, status=status.HTTP_201_CREATED)


class SupportTicketDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, ticket_id):
        """Get details of a specific ticket"""
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Merchant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        try:
            ticket = SupportTicket.objects.get(ticket_id=ticket_id, merchant=merchant)
        except SupportTicket.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Ticket not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Mark ticket as read
        if not ticket.is_read:
            ticket.is_read = True
            ticket.save()
        
        # Get replies
        replies = []
        for reply in ticket.replies.all().order_by('created_at'):
            replies.append({
                'id': reply.id,
                'message': reply.message,
                'is_admin': reply.is_admin,
                'user': reply.user.username if reply.user else 'Unknown',
                'created_at': reply.created_at.isoformat(),
                'has_attachment': bool(reply.attachment)
            })
        
        return Response({
            'status': 'success',
            'data': {
                'ticket_id': ticket.ticket_id,
                'subject': ticket.subject,
                'message': ticket.message,
                'status': ticket.status,
                'ticket_type': ticket.ticket_type,
                'priority': ticket.priority,
                'created_at': ticket.created_at.isoformat(),
                'updated_at': ticket.updated_at.isoformat(),
                'resolved_at': ticket.resolved_at.isoformat() if ticket.resolved_at else None,
                'assigned_to': ticket.assigned_to.username if ticket.assigned_to else None,
                'has_attachment': bool(ticket.attachment),
                'replies': replies
            }
        })
    
    def post(self, request, ticket_id):
        """Add a reply to a ticket"""
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Merchant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        try:
            ticket = SupportTicket.objects.get(ticket_id=ticket_id, merchant=merchant)
        except SupportTicket.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Ticket not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Validate request data
        message = request.data.get('message')
        if not message:
            return Response({
                'status': 'error',
                'message': 'Message is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create reply
        reply = SupportTicketReply.objects.create(
            ticket=ticket,
            user=request.user,
            is_admin=False,
            message=message
        )
        
        # Update ticket status if needed
        if ticket.status in ['resolved', 'closed']:
            ticket.status = 'in_progress'
            ticket.save()
        
        return Response({
            'status': 'success',
            'message': 'Reply added successfully',
            'data': {
                'id': reply.id,
                'created_at': reply.created_at.isoformat()
            }
        }, status=status.HTTP_201_CREATED)
    
    def patch(self, request, ticket_id):
        """Update ticket status"""
        try:
            merchant = Merchant.objects.get(user=request.user)
        except Merchant.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Merchant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        try:
            ticket = SupportTicket.objects.get(ticket_id=ticket_id, merchant=merchant)
        except SupportTicket.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Ticket not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Validate status
        new_status = request.data.get('status')
        if not new_status or new_status not in [s[0] for s in SupportTicket.TICKET_STATUS_CHOICES]:
            return Response({
                'status': 'error',
                'message': 'Invalid status'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Update ticket
        old_status = ticket.status
        ticket.status = new_status
        
        # Set resolved_at timestamp if status changed to resolved
        if new_status == 'resolved' and old_status != 'resolved':
            ticket.resolved_at = timezone.now()
        
        ticket.save()
        
        return Response({
            'status': 'success',
            'message': f'Ticket status updated to {new_status}',
            'data': {
                'ticket_id': ticket.ticket_id,
                'status': ticket.status
            }
        })

# ...existing code...

@staff_member_required
def admin_update_merchant_verification(request, merchant_id):
    """Update merchant verification status"""
    if request.method == 'POST':
        status = request.POST.get('status')
        if status not in dict(Merchant.VERIFICATION_STATUS_CHOICES):
            messages.error(request, f"Invalid verification status: {status}")
            return redirect('payments:admin_merchants')
            
        merchant = get_object_or_404(Merchant, id=merchant_id)
        old_status = merchant.verification_status
        merchant.verification_status = status
        merchant.save()
        
        # Log the change
        logger.info(f"Admin {request.user.username} updated merchant {merchant.id} verification status from {old_status} to {status}")
        
        # Send email notification to merchant
        try:
            # Import the EmailService
            from .email_service import EmailService
            
            # If approving verification
            if status == 'verified':
                EmailService.send_verification_approved_email(merchant)
                messages.success(request, f"Merchant verification approved and notification email sent to {merchant.business_email}")
            # If rejecting verification
            elif status == 'rejected':
                reason = request.POST.get('reason', 'Your verification information could not be validated.')
                EmailService.send_verification_rejected_email(merchant, reason)
                messages.success(request, f"Merchant verification rejected and notification email sent to {merchant.business_email}")
            else:
                messages.success(request, f"Merchant verification status updated to {status}")
        except Exception as e:
            logger.error(f"Failed to send verification email: {str(e)}")
            messages.warning(request, f"Merchant verification status updated, but notification email could not be sent")
        
        # If the request was AJAX, return JSON response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'message': f'Verification status updated to {status}'
            })
            
        # Otherwise redirect back to merchants list
        return redirect('payments:admin_merchants')
        
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

@staff_member_required
def admin_send_merchant_email(request, merchant_id):
    """Send email to merchant"""
    if request.method == 'POST':
        merchant = get_object_or_404(Merchant, id=merchant_id)
        subject = request.POST.get('subject', '')
        message = request.POST.get('message', '')
        
        if not subject or not message:
            messages.error(request, "Email subject and message are required")
            return redirect('payments:admin_merchants')
        
        try:
            # Send email to merchant
            EmailService.send_custom_email(merchant.business_email, subject, message)
            
            # Log the email
            logger.info(f"Admin {request.user.username} sent email to merchant {merchant.id} with subject: {subject}")
            
            messages.success(request, f"Email sent successfully to {merchant.business_email}")
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")
            messages.error(request, f"Failed to send email: {str(e)}")
        
        # If the request was AJAX, return JSON response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'message': f'Email sent to {merchant.business_email}'
            })
            
        # Otherwise redirect back to merchants list
        return redirect('payments:admin_merchants')
        
    # GET request shows email form
    merchant = get_object_or_404(Merchant, id=merchant_id)
    
    context = {
        'page_title': f'Email {merchant.business_name}',
        'merchant': merchant,
    }
    
    return render(request, 'admin_custom/merchant_email.html', context)

@staff_member_required
def approve_transaction(request, reference):
    """
    View to approve or reject a pending transaction
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
    
    transaction = get_object_or_404(Transaction, reference=reference)
    
    # Check if transaction is in a state that can be updated
    if transaction.status not in ['pending', 'flagged']:
        return JsonResponse({
            'status': 'error', 
            'message': f'Cannot update transaction with status {transaction.status}'
        }, status=400)
    
    # Get form data
    status = request.POST.get('status')
    comment = request.POST.get('comment', '')
    
    if status not in ['success', 'failed']:
        return JsonResponse({'status': 'error', 'message': 'Invalid status'}, status=400)
    
    # Update transaction status
    transaction.status = status
    
    # Add completed_at timestamp
    if not transaction.completed_at:
        transaction.completed_at = timezone.now()
        
    # Update metadata with admin comment if provided
    metadata = transaction.get_metadata() or {}
    if comment:
        metadata['admin_comment'] = comment
        metadata['status_updated_by'] = f"Admin: {request.user.username}"
        metadata['status_updated_at'] = timezone.now().isoformat()
        transaction.set_metadata(metadata)
    
    transaction.save()
    
    # If approved as successful, send success notification
    if status == 'success':
        from .email_service import EmailService
        EmailService.send_transaction_success_notification(transaction)
        
        # Update AML cleared status for compliance
        transaction.aml_cleared = True
        transaction.save()
        
    # If marking as failed, send failure notification
    elif status == 'failed':
        from .email_service import EmailService
        # Add the admin comment as error message in metadata
        if comment:
            error_metadata = transaction.get_metadata() or {}
            error_metadata['error_message'] = comment
            transaction.set_metadata(error_metadata)
            transaction.save()
        
        EmailService.send_transaction_failed_notification(transaction)
    
    return JsonResponse({
        'status': 'success',
        'message': f'Transaction marked as {status}',
        'transaction_status': status
    })


@staff_member_required
def send_transaction_receipt(request, reference):
    """
    View to manually send a receipt for a transaction
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
    
    transaction = get_object_or_404(Transaction, reference=reference)
    
    # Only send receipts for successful transactions
    if transaction.status != 'success':
        return JsonResponse({
            'status': 'error',
            'message': 'Receipts can only be sent for successful transactions'
        }, status=400)
    
    # Send receipt email
    from .email_service import EmailService
    success = EmailService.send_transaction_success_notification(transaction)
    
    if success:
        # Update metadata to record that a receipt was sent
        metadata = transaction.get_metadata() or {}
        
        receipt_history = metadata.get('receipt_history', [])
        receipt_history.append({
            'sent_at': timezone.now().isoformat(),
            'sent_by': f"Admin: {request.user.username}",
            'email': transaction.email
        })
        
        metadata['receipt_history'] = receipt_history
        transaction.set_metadata(metadata)
        transaction.save()
        
        return JsonResponse({
            'status': 'success',
            'message': f'Receipt sent successfully to {transaction.email}'
        })
    else:
        return JsonResponse({
            'status': 'error',
            'message': 'Failed to send receipt email'
        }, status=500)

# ...existing code...

@staff_member_required
def admin_add_user(request):
    """View to add a new admin user"""
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        user_type = request.POST.get('user_type', 'staff')
        
        # Validate required fields
        if not all([username, email, password]):
            messages.error(request, 'Please fill in all required fields')
            return redirect('payments:admin_user_management')
        
        # Check if user with this username or email already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, f'User with username "{username}" already exists')
            return redirect('payments:admin_user_management')
        
        if User.objects.filter(email=email).exists():
            messages.error(request, f'User with email "{email}" already exists')
            return redirect('payments:admin_user_management')
        
        # Set user permissions based on user type
        is_staff = True  # All admin users are staff
        is_superuser = False
        
        if user_type == 'admin':
            is_staff = True
        elif user_type == 'superuser':
            is_staff = True
            is_superuser = True
        
        # Create user
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_staff=is_staff,
                is_superuser=is_superuser
            )
            
            messages.success(request, f'User "{username}" created successfully')
            
            # Log the action
            logger.info(f"Admin user {request.user.username} created new user: {username}")
            
        except Exception as e:
            messages.error(request, f'Error creating user: {str(e)}')
            logger.error(f"Error creating user: {str(e)}")
        
        return redirect('payments:admin_user_management')
    
    # GET requests should redirect back to user management page
    return redirect('payments:admin_user_management')


@staff_member_required
def admin_edit_user(request, user_id):
    """View to edit an admin user"""
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        # Get form data
        email = request.POST.get('email')
        first_name = request.POST.get('first_name', '')
        last_name = request.POST.get('last_name', '')
        user_type = request.POST.get('user_type', 'staff')
        reset_password = request.POST.get('reset_password') == 'on'
        
        # Check if email already exists for a different user
        if User.objects.exclude(id=user_id).filter(email=email).exists():
            messages.error(request, f'User with email "{email}" already exists')
            return redirect('payments:admin_user_management')
        
        # Update user details
        user.email = email
        user.first_name = first_name
        user.last_name = last_name
        
        # Set user permissions based on user type
        if user_type == 'staff':
            user.is_staff = True
            user.is_superuser = False
        elif user_type == 'admin':
            user.is_staff = True
            user.is_superuser = False
        elif user_type == 'superuser':
            user.is_staff = True
            user.is_superuser = True
        
        # Reset password if requested
        if reset_password:
            password = request.POST.get('password')
            if password:
                user.set_password(password)
        
        user.save()
        
        messages.success(request, f'User "{user.username}" updated successfully')
        
        # Log the action
        logger.info(f"Admin user {request.user.username} updated user: {user.username}")
        
        return redirect('payments:admin_user_management')
    
    # GET requests should redirect back to user management page
    return redirect('payments:admin_user_management')


@staff_member_required
def admin_delete_user(request, user_id):
    """View to delete an admin user"""
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        username = user.username
        
        # Prevent deleting yourself
        if user == request.user:
            messages.error(request, 'You cannot delete your own account')
            return redirect('payments:admin_user_management')
        
        # Delete the user
        try:
            user.delete()
            messages.success(request, f'User "{username}" deleted successfully')
            
            # Log the action
            logger.info(f"Admin user {request.user.username} deleted user: {username}")
            
        except Exception as e:
            messages.error(request, f'Error deleting user: {str(e)}')
            logger.error(f"Error deleting user: {str(e)}")
        
        return redirect('payments:admin_user_management')
    
    # GET requests should redirect back to user management page
    return redirect('payments:admin_user_management')


@staff_member_required
def admin_toggle_user_status(request, user_id):
    """View to activate or deactivate an admin user"""
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # Prevent deactivating yourself
        if user == request.user:
            messages.error(request, 'You cannot change your own status')
            return redirect('payments:admin_user_management')
        
        if action == 'activate':
            user.is_active = True
            user.save()
            messages.success(request, f'User "{user.username}" activated successfully')
            
            # Log the action
            logger.info(f"Admin user {request.user.username} activated user: {user.username}")
            
        elif action == 'deactivate':
            user.is_active = False
            user.save()
            messages.success(request, f'User "{user.username}" deactivated successfully')
            
            # Log the action
            logger.info(f"Admin user {request.user.username} deactivated user: {user.username}")
        
        return redirect('payments:admin_user_management')
    
    # GET requests should redirect back to user management page
    return redirect('payments:admin_user_management')


@staff_member_required
def admin_import_users(request):
    """View to import admin users from CSV file"""
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        
        if not csv_file or not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a valid CSV file')
            return redirect('payments:admin_user_management')
        
        # Process the CSV file
        try:
            # Read the CSV file
            csv_data = csv_file.read().decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(csv_data))
            
            # Track success and failures
            success_count = 0
            failure_count = 0
            
            # Process each row
            for row in csv_reader:
                try:
                    # Check required fields
                    if not all(key in row for key in ['username', 'email', 'password']):
                        continue
                    
                    # Check if user already exists
                    username = row['username']
                    email = row['email']
                    
                    if User.objects.filter(Q(username=username) | Q(email=email)).exists():
                        failure_count += 1
                        continue
                    
                    # Create the user
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=row['password'],
                        first_name=row.get('first_name', ''),
                        last_name=row.get('last_name', ''),
                        is_staff=row.get('is_staff', 'True').lower() == 'true',
                        is_superuser=row.get('is_superuser', 'False').lower() == 'true'
                    )
                    
                    success_count += 1
                    
                except Exception as e:
                    failure_count += 1
                    logger.error(f"Error importing user: {str(e)}")
            
            # Report results
            if success_count > 0:
                messages.success(request, f'Successfully imported {success_count} user(s)')
            
            if failure_count > 0:
                messages.warning(request, f'Failed to import {failure_count} user(s)')
            
            # Log the action
            logger.info(f"Admin user {request.user.username} imported {success_count} users with {failure_count} failures")
            
        except Exception as e:
            messages.error(request, f'Error processing CSV file: {str(e)}')
            logger.error(f"Error processing CSV file: {str(e)}")
        
        return redirect('payments:admin_user_management')
    
    # GET requests should redirect back to user management page
    return redirect('payments:admin_user_management')

# ...existing code...

@staff_member_required
def admin_documentation(request):
    """View for the admin documentation page"""
    # Get all API endpoints from urls.py for documentation
    api_endpoints = [
        {
            'name': 'Initialize Payment',
            'endpoint': '/api/v1/payments/initialize/',
            'method': 'POST',
            'description': 'Initialize a payment transaction',
            'parameters': {
                'amount': 'Amount to charge (in the smallest currency unit e.g. cents)',
                'email': 'Customer email address',
                'reference': 'Unique transaction reference (optional)',
                'callback_url': 'URL to redirect after payment (optional)',
                'metadata': 'Additional information to store with transaction (optional)'
            },
            'example_request': '''
{
    "amount": 5000,
    "email": "customer@example.com",
    "reference": "unique-transaction-ref-123",
    "callback_url": "https://merchant-website.com/payment-callback",
    "metadata": {
        "order_id": "12345",
        "customer_name": "John Doe"
    }
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Payment initialized",
    "data": {
        "reference": "TXN-12345-ABCDE",
        "checkout_url": "https://hamsukypay.com/checkout/TXN-12345-ABCDE",
        "amount": 5000
    }
}'''
        },
        {
            'name': 'Verify Payment',
            'endpoint': '/api/v1/payments/verify/<reference>/',
            'method': 'GET',
            'description': 'Verify the status of a payment transaction',
            'parameters': {
                'reference': 'Transaction reference (in URL path)'
            },
            'example_request': 'GET /api/v1/payments/verify/TXN-12345-ABCDE/',
            'example_response': '''
{
    "status": "success",
    "message": "Payment verified",
    "data": {
        "reference": "TXN-12345-ABCDE",
        "amount": 5000,
        "status": "successful",
        "paid_at": "2025-05-03T10:45:32Z",
        "payment_method": "card",
        "metadata": {
            "order_id": "12345",
            "customer_name": "John Doe"
        }
    }
}'''
        },
        {
            'name': 'Process Refund',
            'endpoint': '/api/v1/payments/refund/<reference>/',
            'method': 'POST',
            'description': 'Process a refund for a completed transaction',
            'parameters': {
                'reference': 'Transaction reference (in URL path)',
                'amount': 'Amount to refund (optional, defaults to full amount)',
                'reason': 'Reason for refund (optional)'
            },
            'example_request': '''
{
    "amount": 2500,
    "reason": "Customer requested partial refund"
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Refund processed",
    "data": {
        "reference": "TXN-12345-ABCDE",
        "refund_reference": "RFD-67890-FGHIJ",
        "amount": 2500,
        "status": "processing"
    }
}'''
        },
        {
            'name': 'Create Payment Plan',
            'endpoint': '/api/v1/payments/plans/create/',
            'method': 'POST',
            'description': 'Create a subscription payment plan',
            'parameters': {
                'name': 'Name of the plan',
                'amount': 'Amount to charge (in the smallest currency unit)',
                'interval': 'Billing interval (daily, weekly, monthly, quarterly, biannually, annually)',
                'description': 'Plan description (optional)'
            },
            'example_request': '''
{
    "name": "Premium Plan",
    "amount": 15000,
    "interval": "monthly",
    "description": "Premium subscription with all features"
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Plan created",
    "data": {
        "id": "PLN-12345",
        "name": "Premium Plan",
        "amount": 15000,
        "interval": "monthly",
        "description": "Premium subscription with all features"
    }
}'''
        },
        {
            'name': 'Create Customer',
            'endpoint': '/api/v1/customers/create/',
            'method': 'POST',
            'description': 'Create a customer record',
            'parameters': {
                'email': 'Customer email address',
                'first_name': 'Customer first name (optional)',
                'last_name': 'Customer last name (optional)',
                'phone': 'Customer phone number (optional)',
                'metadata': 'Additional customer information (optional)'
            },
            'example_request': '''
{
    "email": "customer@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "phone": "+1234567890",
    "metadata": {
        "address": "123 Main St",
        "city": "New York"
    }
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Customer created",
    "data": {
        "id": "CUS-12345",
        "email": "customer@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+1234567890",
        "created_at": "2025-05-03T09:15:22Z"
    }
}'''
        },
        {
            'name': 'Create Subscription',
            'endpoint': '/api/v1/payments/subscriptions/create/',
            'method': 'POST',
            'description': 'Create a subscription for a customer',
            'parameters': {
                'customer': 'Customer ID or email',
                'plan': 'Payment plan ID',
                'start_date': 'Subscription start date (optional, defaults to now)',
                'metadata': 'Additional subscription data (optional)'
            },
            'example_request': '''
{
    "customer": "CUS-12345",
    "plan": "PLN-12345",
    "start_date": "2025-06-01T00:00:00Z",
    "metadata": {
        "referrer": "website_promotion"
    }
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Subscription created",
    "data": {
        "id": "SUB-12345",
        "customer": "CUS-12345",
        "plan": "PLN-12345",
        "status": "active",
        "start_date": "2025-06-01T00:00:00Z",
        "next_payment_date": "2025-07-01T00:00:00Z"
    }
}'''
        },
        {
            'name': 'Tokenize Card',
            'endpoint': '/api/v1/payments/tokenize/',
            'method': 'POST',
            'description': 'Tokenize a card for future recurring payments',
            'parameters': {
                'customer': 'Customer ID or email',
                'card_number': 'Card number',
                'expiry_month': 'Card expiry month (1-12)',
                'expiry_year': 'Card expiry year (e.g., 2025)',
                'cvv': 'Card CVV/CVC'
            },
            'example_request': '''
{
    "customer": "CUS-12345",
    "card_number": "4111111111111111",
    "expiry_month": 12,
    "expiry_year": 2025,
    "cvv": "123"
}''',
            'example_response': '''
{
    "status": "success",
    "message": "Card tokenized",
    "data": {
        "token": "tok_visa_12345",
        "last4": "1111",
        "exp_month": 12,
        "exp_year": 2025,
        "card_type": "visa"
    }
}'''
        }
    ]
    
    # Documentation categories
    documentation_categories = [
        {
            'name': 'Getting Started',
            'slug': 'getting-started',
            'topics': [
                {
                    'title': 'Introduction',
                    'content': 'HamsukyPay is a comprehensive payment gateway that allows businesses to accept payments online. '
                               'The platform supports various payment methods including cards, mobile money, bank transfers, and more.',
                },
                {
                    'title': 'Authentication',
                    'content': 'All API requests must include your API keys in the request headers. '
                               'Your API keys can be found in the Merchant Dashboard under API Keys section. '
                               'Never share your secret API keys in client-side code or repositories.',
                },
                {
                    'title': 'API Base URL',
                    'content': 'The base URL for all API requests is: https://api.hamsukypay.com'
                }
            ]
        },
        {
            'name': 'Core Concepts',
            'slug': 'core-concepts',
            'topics': [
                {
                    'title': 'Transactions',
                    'content': 'A transaction represents any money movement in the system. Each transaction has a unique reference that can be used to track its status.',
                },
                {
                    'title': 'Customers',
                    'content': 'Customers represent your users in HamsukyPay. Creating customer records allows you to store payment information and track transaction history.',
                },
                {
                    'title': 'Payment Plans',
                    'content': 'Payment plans define recurring billing configurations including amount, currency, and billing interval.',
                },
                {
                    'title': 'Subscriptions',
                    'content': 'Subscriptions link customers to payment plans for recurring billing. They can be for fixed periods or open-ended.',
                }
            ]
        },
        {
            'name': 'Integration Guides',
            'slug': 'integration-guides',
            'topics': [
                {
                    'title': 'Web Integration',
                    'content': 'For web applications, we recommend using our JavaScript SDK to create a seamless checkout experience. '
                               'The SDK handles payment form display, validation, and secure submission.',
                },
                {
                    'title': 'Mobile Integration',
                    'content': 'For native mobile applications, use our iOS and Android SDKs to implement secure, native payment experiences.',
                },
                {
                    'title': 'Server Integration',
                    'content': 'For server-side integration, you can use our REST APIs with any programming language that can make HTTP requests.',
                },
                {
                    'title': 'Webhook Setup',
                    'content': 'Webhooks allow you to receive real-time notifications about payment events. Set up webhooks to automate actions in your system when transactions occur.',
                }
            ]
        },
        {
            'name': 'Advanced Features',
            'slug': 'advanced-features',
            'topics': [
                {
                    'title': 'Split Payments',
                    'content': 'Configure split payments to automatically share transaction amounts between multiple accounts. Useful for marketplaces and platforms.',
                },
                {
                    'title': 'Transaction Analytics',
                    'content': 'Use our analytics APIs to retrieve transaction statistics, trends, and insights for your business.',
                },
                {
                    'title': 'Multi-currency Processing',
                    'content': 'Accept payments in multiple currencies and automate conversion and settlement.',
                },
                {
                    'title': 'Fraud Prevention',
                    'content': 'Learn about our built-in fraud detection tools and how to configure them for your business needs.',
                }
            ]
        },
        {
            'name': 'Compliance & Security',
            'slug': 'compliance-security',
            'topics': [
                {
                    'title': 'PCI Compliance',
                    'content': 'HamsukyPay is PCI DSS Level 1 compliant. Using our checkout form or SDKs ensures your integration remains compliant.',
                },
                {
                    'title': 'Data Security',
                    'content': 'All data is encrypted in transit and at rest. Learn about our security practices and how they protect your business and customers.',
                },
                {
                    'title': 'KYC Requirements',
                    'content': 'Understand the Know Your Customer (KYC) requirements for merchants and how to complete the verification process.',
                },
                {
                    'title': 'AML Compliance',
                    'content': 'Learn about our Anti-Money Laundering policies and how we monitor transactions for suspicious activity.',
                }
            ]
        }
    ]
    
    # Sample code snippets in different languages
    code_samples = {
        'python': '''
import requests

api_key = 'YOUR_SECRET_KEY'
headers = {
    'Authorization': f'Bearer {api_key}',
    'Content-Type': 'application/json'
}

data = {
    'amount': 5000,
    'email': 'customer@example.com',
    'reference': 'unique-ref-123'
}

response = requests.post(
    'https://api.hamsukypay.com/api/v1/payments/initialize/',
    json=data,
    headers=headers
)

print(response.json())
''',
        'javascript': '''
// Using fetch API
const apiKey = 'YOUR_SECRET_KEY';
const data = {
    amount: 5000,
    email: 'customer@example.com',
    reference: 'unique-ref-123'
};

fetch('https://api.hamsukypay.com/api/v1/payments/initialize/', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
    },
    body: JSON.stringify(data)
})
.then(response => response.json())
.then(data => console.log(data))
.catch(error => console.error('Error:', error));
''',
        'php': '''
<?php
$api_key = 'YOUR_SECRET_KEY';
$data = array(
    'amount' => 5000,
    'email' => 'customer@example.com',
    'reference' => 'unique-ref-123'
);

$curl = curl_init();
curl_setopt_array($curl, array(
    CURLOPT_URL => 'https://api.hamsukypay.com/api/v1/payments/initialize/',
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_CUSTOMREQUEST => 'POST',
    CURLOPT_POSTFIELDS => json_encode($data),
    CURLOPT_HTTPHEADER => array(
        'Authorization: Bearer ' . $api_key,
        'Content-Type: application/json'
    ),
));

$response = curl_exec($curl);
$err = curl_error($curl);
curl_close($curl);

if ($err) {
    echo "cURL Error #:" . $err;
} else {
    echo $response;
}
?>
''',
        'java': '''
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class HamsukyPayExample {
    public static void main(String[] args) {
        try {
            String apiKey = "YOUR_SECRET_KEY";
            String requestBody = """
                {
                    "amount": 5000,
                    "email": "customer@example.com",
                    "reference": "unique-ref-123"
                }
                """;

            HttpClient client = HttpClient.newHttpClient();
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create("https://api.hamsukypay.com/api/v1/payments/initialize/"))
                .header("Authorization", "Bearer " + apiKey)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(requestBody))
                .build();

            HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
            System.out.println(response.body());
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
'''
    }
    
    context = {
        'api_endpoints': api_endpoints,
        'documentation_categories': documentation_categories,
        'code_samples': code_samples
    }
    
    return render(request, 'admin_custom/documentation.html', context)

def contact_view(request):
    """View for the contact page"""
    
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        subject = request.POST.get('subject')
        message = request.POST.get('message')
        
        # Validate form fields
        if not all([name, email, subject, message]):
            return render(request, 'payments/contact.html', {
                'error': 'Please fill in all required fields',
                'form_data': request.POST
            })
            
        # Basic email validation
        if '@' not in email or '.' not in email:
            return render(request, 'payments/contact.html', {
                'error': 'Please enter a valid email address',
                'form_data': request.POST
            })
            
        try:
            # Here you would typically send an email
            # For now, we'll just simulate successful submission
            
            # You can implement real email sending using Django's send_mail
            # from django.core.mail import send_mail
            # send_mail(
            #     f'Contact Form: {subject}',
            #     f'Message from {name} ({email}):\n\n{message}',
            #     email,
            #     ['support@hamsukypay.com'],
            #     fail_silently=False,
            # )
            
            return render(request, 'payments/contact.html', {
                'success': 'Your message has been sent. We will get back to you soon!',
                'form_data': {}  # Clear form data on success
            })
            
        except Exception as e:
            logger.error(f"Error in contact form: {str(e)}")
            return render(request, 'payments/contact.html', {
                'error': 'There was a problem sending your message. Please try again later.',
                'form_data': request.POST
            })
    
    return render(request, 'payments/contact.html', {
        'page_title': 'Contact Us',
        'current_year': timezone.now().year,
        'form_data': {}
    })