from django.db import models
import uuid
import secrets
from django.utils import timezone
import json
from django.contrib.auth.models import User
from django.conf import settings


class Customer(models.Model):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Enhanced customer model with metadata for saved payment methods
    metadata = models.TextField(null=True, blank=True)
    
    def set_metadata(self, data):
        self.metadata = json.dumps(data)
    
    def get_metadata(self):
        if self.metadata:
            try:
                return json.loads(self.metadata)
            except (TypeError, json.JSONDecodeError):
                return {}
        return {}
    
    def save_payment_method(self, payment_data):
        """Save customer payment method in a secure way for recurring billing."""
        metadata = self.get_metadata() or {}
        
        # In production, this would be tokenized/encrypted data from payment provider
        payment_methods = metadata.get('payment_methods', [])
        
        # Add new payment method
        payment_methods.append({
            'provider': payment_data.get('provider', 'paystack'),
            'authorization_code': payment_data.get('authorization_code'),
            'card_type': payment_data.get('card_type'),
            'last4': payment_data.get('last4'),
            'exp_month': payment_data.get('exp_month'),
            'exp_year': payment_data.get('exp_year'),
            'bank': payment_data.get('bank'),
            'created_at': timezone.now().isoformat()
        })
        
        metadata['payment_methods'] = payment_methods
        self.set_metadata(metadata)
        self.save()

    def __str__(self):
        return self.email


class Merchant(models.Model):
    VERIFICATION_STATUS_CHOICES = (
        ('unverified', 'Unverified'),
        ('pending', 'Pending'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    )
    
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=255)
    business_email = models.EmailField(unique=True)
    business_phone = models.CharField(max_length=20)
    business_address = models.TextField()
    business_description = models.TextField(blank=True, null=True)
    logo = models.CharField(max_length=255, blank=True, null=True)  # Path to logo file
    website = models.URLField(blank=True, null=True)
    industry = models.CharField(max_length=100, blank=True, null=True)
    verification_status = models.CharField(
        max_length=20, 
        choices=VERIFICATION_STATUS_CHOICES, 
        default='unverified'
    )
    public_key = models.CharField(max_length=64, unique=True)
    secret_key = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    
    # Transaction fee structure
    local_transaction_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=1.5)
    local_transaction_flat_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Removed the NGN 100 flat fee
    local_transaction_fee_waiver_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=2500)  # Kept for backward compatibility
    local_transaction_fee_cap = models.DecimalField(max_digits=10, decimal_places=2, default=1500)  # Maximum fee cap for local transactions
    international_transaction_fee_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=3.9)
    international_transaction_flat_fee = models.DecimalField(max_digits=10, decimal_places=2, default=100)  # NGN 100 flat fee for international
    
    # Settlement options
    SETTLEMENT_CURRENCY_CHOICES = (
        ('NGN', 'Nigerian Naira'),
        ('USD', 'US Dollar'),
    )
    settlement_currency = models.CharField(max_length=3, choices=SETTLEMENT_CURRENCY_CHOICES, default='NGN')
    settlement_bank = models.CharField(max_length=255, blank=True, null=True)
    settlement_account = models.CharField(max_length=20, blank=True, null=True)
    settlement_account_name = models.CharField(max_length=255, blank=True, null=True)
    
    # For backward compatibility with existing code
    @property
    def transaction_fee_percentage(self):
        return self.local_transaction_fee_percentage
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # Changed from auto_now_add to auto_now
    
    def __str__(self):
        return self.business_name
    
    @staticmethod
    def generate_public_key():
        return f"pk_{'live' if settings.PRODUCTION else 'test'}_{secrets.token_hex(16)}"
    
    @staticmethod
    def generate_secret_key():
        return f"sk_{'live' if settings.PRODUCTION else 'test'}_{secrets.token_hex(24)}"


class PaymentMethod(models.Model):
    """Model for storing customer payment methods securely"""
    METHOD_TYPE_CHOICES = (
        ('card', 'Card'),
        ('bank_account', 'Bank Account'),
        ('mobile_money', 'Mobile Money'),
        ('ussd', 'USSD'),
    )
    
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payment_methods')
    provider = models.CharField(max_length=50, default='internal')
    method_type = models.CharField(max_length=20, choices=METHOD_TYPE_CHOICES, default='card')
    card_type = models.CharField(max_length=20, null=True, blank=True)
    last4 = models.CharField(max_length=4, null=True, blank=True)
    exp_month = models.CharField(max_length=2, null=True, blank=True)
    exp_year = models.CharField(max_length=4, null=True, blank=True)
    bank_name = models.CharField(max_length=100, null=True, blank=True)
    account_name = models.CharField(max_length=100, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    reference = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        if self.method_type == 'card' and self.last4:
            return f"{self.card_type} **** **** **** {self.last4}"
        elif self.method_type == 'bank_account' and self.bank_name:
            return f"{self.bank_name} - {self.account_name}"
        else:
            return f"{self.method_type} - {self.created_at.strftime('%Y-%m-%d')}"


class Transaction(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
        ('flagged', 'Flagged for Review'),
    )
    
    # Expanded currency choices using the wider list from currency_service.py
    CURRENCY_CHOICES = (
        ('NGN', 'Nigerian Naira'),
        ('USD', 'US Dollar'),
        ('EUR', 'Euro'),
        ('GBP', 'British Pound'),
        ('KES', 'Kenyan Shilling'),
        ('ZAR', 'South African Rand'),
        ('GHS', 'Ghanaian Cedi'),
        ('CAD', 'Canadian Dollar'),
        ('AUD', 'Australian Dollar'),
        ('JPY', 'Japanese Yen'),
        ('INR', 'Indian Rupee'),
        ('CNY', 'Chinese Yuan'),
        ('AED', 'UAE Dirham'),
        ('EGP', 'Egyptian Pound'),
        ('UGX', 'Ugandan Shilling'),
        ('TZS', 'Tanzanian Shilling'),
        ('RWF', 'Rwandan Franc'),
        ('BRL', 'Brazilian Real'),
        ('MXN', 'Mexican Peso'),
        ('SGD', 'Singapore Dollar'),
        ('XOF', 'West African CFA Franc'),
        ('XAF', 'Central African CFA Franc'),
    )
    
    reference = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='NGN')
    
    # Fields for currency conversion
    original_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    original_currency = models.CharField(max_length=3, null=True, blank=True)
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True)
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    description = models.TextField(blank=True, null=True)
    metadata = models.TextField(null=True, blank=True)  # Changed from JSONField to TextField
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    payment_provider = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.SET_NULL, null=True, related_name='transactions')
    
    # Compliance fields
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    device_fingerprint = models.CharField(max_length=255, null=True, blank=True)
    risk_score = models.FloatField(default=0.0)
    compliance_status = models.CharField(
        max_length=20, 
        default='pending',
        choices=(
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('review', 'Under Review'),
        )
    )
    kyc_verified = models.BooleanField(default=False)
    aml_cleared = models.BooleanField(default=False)
    pci_compliant = models.BooleanField(default=True)
    
    def __str__(self):
        return self.reference
    
    @staticmethod
    def generate_reference():
        return f"HMSKY-{secrets.token_hex(5).upper()}-{str(uuid.uuid4())[:8].upper()}"
    
    def set_metadata(self, data):
        self.metadata = json.dumps(data)
    
    def get_metadata(self):
        if self.metadata:
            try:
                return json.loads(self.metadata)
            except (TypeError, json.JSONDecodeError):
                return {}
        return {}
        
    def is_high_value(self):
        """Check if this is a high-value transaction that requires additional scrutiny"""
        from .currency_service import CurrencyService
        
        # Convert to USD for standard comparison
        if self.currency != 'USD':
            try:
                amount_usd = CurrencyService.convert_amount(self.amount, self.currency, 'USD')
            except:
                # If conversion fails, use original amount and a conservative threshold
                return self.amount > 10000
        else:
            amount_usd = self.amount
            
        # $10,000+ is typically a threshold for enhanced scrutiny
        return amount_usd >= 10000


class PaymentPlan(models.Model):
    INTERVAL_CHOICES = (
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annually', 'Annually'),
    )
    
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='NGN')
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES)
    description = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='payment_plans', null=True)
    
    def __str__(self):
        return self.name


class Subscription(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('paused', 'Paused'),
        ('expired', 'Expired'),
        ('past_due', 'Past Due'),
    )
    
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE)
    reference = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    start_date = models.DateTimeField(default=timezone.now)
    next_payment_date = models.DateTimeField()
    last_payment_date = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    number_of_retries = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    metadata = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.customer.email} - {self.plan.name}"
    
    def set_metadata(self, data):
        self.metadata = json.dumps(data)
    
    def get_metadata(self):
        if self.metadata:
            try:
                return json.loads(self.metadata)
            except (TypeError, json.JSONDecodeError):
                return {}
        return {}
    
    def cancel(self):
        self.status = 'cancelled'
        self.cancelled_at = timezone.now()
        self.save()
    
    def pause(self):
        self.status = 'paused'
        self.save()
    
    def resume(self):
        self.status = 'active'
        self.save()
    
    def process_payment(self):
        """Process subscription payment. Returns True if payment is successful, False otherwise."""
        # In production, this would process payment using the customer's saved payment method
        from .payment_processor import process_subscription_payment
        success, transaction = process_subscription_payment(self)
        
        if success:
            self.last_payment_date = timezone.now()
            
            # Calculate next payment date based on plan interval
            if self.plan.interval == 'daily':
                self.next_payment_date = timezone.now() + timezone.timedelta(days=1)
            elif self.plan.interval == 'weekly':
                self.next_payment_date = timezone.now() + timezone.timedelta(days=7)
            elif self.plan.interval == 'monthly':
                self.next_payment_date = timezone.now() + timezone.timedelta(days=30)
            elif self.plan.interval == 'quarterly':
                self.next_payment_date = timezone.now() + timezone.timedelta(days=90)
            elif self.plan.interval == 'annually':
                self.next_payment_date = timezone.now() + timezone.timedelta(days=365)
            
            self.number_of_retries = 0
            self.save()
            return True, transaction
        else:
            self.number_of_retries += 1
            if self.number_of_retries >= self.max_retries:
                self.status = 'past_due'
            self.save()
            return False, None


# New Analytics models
class AnalyticsData(models.Model):
    """Daily analytics data for merchants"""
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='analytics')
    date = models.DateField()
    total_transactions = models.IntegerField(default=0)
    successful_transactions = models.IntegerField(default=0)
    failed_transactions = models.IntegerField(default=0)
    total_volume = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='NGN')
    new_customers = models.IntegerField(default=0)
    
    class Meta:
        unique_together = ('merchant', 'date', 'currency')
    
    def __str__(self):
        return f"{self.merchant.business_name} - {self.date}"


class PaymentMethodStats(models.Model):
    """Payment method usage statistics for analytics"""
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='payment_method_stats')
    date = models.DateField()
    payment_method = models.CharField(max_length=50)
    transaction_count = models.IntegerField(default=0)
    volume = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='NGN')
    
    class Meta:
        unique_together = ('merchant', 'date', 'payment_method', 'currency')


class GeographicStats(models.Model):
    """Geographic distribution of transactions for analytics"""
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='geographic_stats')
    date = models.DateField()
    country = models.CharField(max_length=2)  # ISO country code
    transaction_count = models.IntegerField(default=0)
    volume = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='NGN')
    
    class Meta:
        unique_together = ('merchant', 'date', 'country', 'currency')


# Fraud detection models
class RiskAssessment(models.Model):
    RISK_LEVEL_CHOICES = (
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('blocked', 'Blocked'),
    )
    
    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE, related_name='risk_assessment')
    risk_score = models.FloatField(default=0.0)  # 0-100 score, higher means more risky
    risk_level = models.CharField(max_length=10, choices=RISK_LEVEL_CHOICES, default='low')
    is_flagged = models.BooleanField(default=False)
    rules_triggered = models.TextField(null=True, blank=True)  # JSON list of triggered rules
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_fingerprint = models.CharField(max_length=255, null=True, blank=True)
    geolocation = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def set_rules_triggered(self, rules):
        self.rules_triggered = json.dumps(rules)
    
    def get_rules_triggered(self):
        if self.rules_triggered:
            try:
                return json.loads(self.rules_triggered)
            except (TypeError, json.JSONDecodeError):
                return []
        return []


class FraudRule(models.Model):
    RULE_TYPE_CHOICES = (
        ('velocity', 'Velocity Check'),
        ('amount', 'Amount Threshold'),
        ('location', 'Location Based'),
        ('device', 'Device Fingerprint'),
        ('pattern', 'Transaction Pattern'),
        ('custom', 'Custom Rule'),
    )
    
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    rule_type = models.CharField(max_length=20, choices=RULE_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    risk_score = models.IntegerField(default=10)  # Score to add when rule is triggered
    conditions = models.TextField()  # JSON object with rule conditions
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='fraud_rules', null=True, blank=True)
    is_global = models.BooleanField(default=False)  # If True, applies to all merchants
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    
    def get_conditions(self):
        try:
            return json.loads(self.conditions)
        except (TypeError, json.JSONDecodeError):
            return {}
    
    def set_conditions(self, conditions_dict):
        self.conditions = json.dumps(conditions_dict)


class BlacklistedIP(models.Model):
    ip_address = models.GenericIPAddressField(unique=True)
    reason = models.TextField()
    added_at = models.DateTimeField(auto_now_add=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='blacklisted_ips', null=True, blank=True)
    is_global = models.BooleanField(default=False)  # If True, applies to all merchants


class BlacklistedCard(models.Model):
    bin = models.CharField(max_length=6)  # First 6 digits of card
    last4 = models.CharField(max_length=4)  # Last 4 digits of card
    reason = models.TextField()
    added_at = models.DateTimeField(auto_now_add=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='blacklisted_cards', null=True, blank=True)
    is_global = models.BooleanField(default=False)  # If True, applies to all merchants
    
    class Meta:
        unique_together = ('bin', 'last4', 'merchant')


# Add multi-currency support
class ExchangeRate(models.Model):
    """Exchange rates for currency conversion"""
    base_currency = models.CharField(max_length=3, default='NGN')
    target_currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=15, decimal_places=6)
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('base_currency', 'target_currency')
    
    def __str__(self):
        return f"{self.base_currency}/{self.target_currency}: {self.rate}"


# Update the Merchant model to support multiple payment providers and currencies
class MerchantPaymentProvider(models.Model):
    """Payment provider configuration for a merchant"""
    PROVIDER_CHOICES = (
        ('paystack', 'Paystack'),
        ('flutterwave', 'Flutterwave'),
        ('stripe', 'Stripe'),
        ('paypal', 'PayPal'),
    )
    
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='payment_providers')
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    api_key = models.CharField(max_length=255)
    secret_key = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    configuration = models.TextField(null=True, blank=True)  # JSON for additional config
    
    class Meta:
        unique_together = ('merchant', 'provider')
    
    def get_configuration(self):
        if self.configuration:
            try:
                return json.loads(self.configuration)
            except (TypeError, json.JSONDecodeError):
                return {}
        return {}
    
    def set_configuration(self, config_dict):
        self.configuration = json.dumps(config_dict)


class MerchantCurrency(models.Model):
    """Supported currencies for a merchant"""
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='currencies')
    currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    auto_convert = models.BooleanField(default=False)  # Auto convert to merchant's default currency
    
    class Meta:
        unique_together = ('merchant', 'currency')
    
    def __str__(self):
        return f"{self.merchant.business_name} - {self.currency}"


# Compliance models
class ComplianceLog(models.Model):
    """Logs of compliance checks performed"""
    CHECK_TYPE_CHOICES = (
        ('transaction', 'Transaction Check'),
        ('customer', 'Customer KYC Check'),
        ('merchant', 'Merchant Compliance Check'),
    )
    
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='compliance_logs', null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='compliance_logs', null=True, blank=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='compliance_logs', null=True, blank=True)
    check_type = models.CharField(max_length=20, choices=CHECK_TYPE_CHOICES)
    is_compliant = models.BooleanField()
    risk_score = models.FloatField(default=0.0)
    details = models.TextField()  # JSON field with check details
    created_at = models.DateTimeField(auto_now_add=True)
    
    def get_details(self):
        try:
            return json.loads(self.details)
        except (TypeError, json.JSONDecodeError):
            return {}
            
    def __str__(self):
        entity = self.transaction or self.customer or self.merchant
        return f"{self.check_type} - {entity} - {'Pass' if self.is_compliant else 'Fail'}"


class MerchantCompliance(models.Model):
    """Tracks merchant compliance with regulatory requirements"""
    merchant = models.OneToOneField(Merchant, on_delete=models.CASCADE, related_name='compliance_info')
    pci_compliance_level = models.IntegerField(default=4)  # 1-4, where 1 is most stringent
    pci_compliance_complete = models.BooleanField(default=False)
    pci_last_verified = models.DateTimeField(null=True, blank=True)
    
    aml_program_accepted = models.BooleanField(default=False)
    aml_program_accepted_date = models.DateTimeField(null=True, blank=True)
    
    kyc_procedures_accepted = models.BooleanField(default=False)
    kyc_procedures_accepted_date = models.DateTimeField(null=True, blank=True)
    
    data_protection_accepted = models.BooleanField(default=False)
    data_protection_accepted_date = models.DateTimeField(null=True, blank=True)
    
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_date = models.DateTimeField(null=True, blank=True)
    
    high_risk_category = models.BooleanField(default=False)
    enhanced_due_diligence_complete = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Compliance - {self.merchant.business_name}"
        
    def is_fully_compliant(self):
        """Check if merchant is fully compliant with all requirements"""
        basic_compliance = (
            self.pci_compliance_complete and 
            self.aml_program_accepted and 
            self.kyc_procedures_accepted and
            self.data_protection_accepted and
            self.terms_accepted
        )
        
        # If high risk, also need enhanced due diligence
        if self.high_risk_category:
            return basic_compliance and self.enhanced_due_diligence_complete
            
        return basic_compliance
        
        
class CustomerKYC(models.Model):
    """KYC information for customers"""
    KYC_LEVEL_CHOICES = (
        (0, 'Not Verified'),
        (1, 'Basic Verification'),      # Email verified
        (2, 'Standard Verification'),   # ID verified
        (3, 'Enhanced Verification'),   # Address & documents verified
    )
    
    ID_TYPE_CHOICES = (
        ('passport', 'Passport'),
        ('id_card', 'National ID Card'),
        ('driver_license', 'Driver\'s License'),
        ('voter_card', 'Voter\'s Card'),
    )
    
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='kyc_info')
    kyc_level = models.IntegerField(choices=KYC_LEVEL_CHOICES, default=0)
    kyc_verified = models.BooleanField(default=False)
    verification_date = models.DateTimeField(null=True, blank=True)
    
    id_type = models.CharField(max_length=20, choices=ID_TYPE_CHOICES, null=True, blank=True)
    id_number = models.CharField(max_length=50, null=True, blank=True)
    id_expiry = models.DateField(null=True, blank=True)
    id_country = models.CharField(max_length=2, null=True, blank=True)  # ISO country code
    
    address_verified = models.BooleanField(default=False)
    address_verification_date = models.DateTimeField(null=True, blank=True)
    
    is_pep = models.BooleanField(default=False)  # Politically exposed person
    pep_details = models.TextField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"KYC - {self.customer.email} - Level {self.kyc_level}"


class Webhook(models.Model):
    """Model to store webhook endpoints for merchants"""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('failed', 'Failed'),  # For webhooks that consistently fail
    ]
    
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='webhooks')
    url = models.URLField(max_length=255)
    event_type = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True, null=True)
    secret = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_triggered = models.DateTimeField(null=True, blank=True)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['merchant', 'url', 'event_type']
    
    def __str__(self):
        return f"Webhook {self.id}: {self.event_type} for {self.merchant.business_name}"
    
    @property
    def success_rate(self):
        """Calculate the success rate as a percentage"""
        total = self.success_count + self.failure_count
        if total == 0:
            return 100  # No attempts yet
        return round((self.success_count / total) * 100)


class SupportTicket(models.Model):
    """Model to store customer support tickets and track their resolution"""
    TICKET_STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    
    TICKET_TYPE_CHOICES = [
        ('account', 'Account Issues'),
        ('payment', 'Payment Processing'),
        ('integration', 'API Integration'),
        ('billing', 'Billing & Invoices'),
        ('feature', 'Feature Request'),
        ('other', 'Other'),
    ]
    
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]
    
    ticket_id = models.CharField(max_length=20, unique=True)
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE, related_name='support_tickets')
    subject = models.CharField(max_length=255)
    message = models.TextField()
    ticket_type = models.CharField(max_length=20, choices=TICKET_TYPE_CHOICES)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=15, choices=TICKET_STATUS_CHOICES, default='open')
    
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tickets')
    attachment = models.FileField(upload_to='support_attachments/', null=True, blank=True)
    
    is_read = models.BooleanField(default=False)
    is_notified = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Ticket {self.ticket_id}: {self.subject}"
    
    def save(self, *args, **kwargs):
        # Generate ticket ID if it's a new ticket
        if not self.ticket_id:
            import random
            import string
            # Generate ticket ID in format: SUP-XXXXXX (where X is alphanumeric)
            random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            self.ticket_id = f"SUP-{random_str}"
        
        # If status changed to resolved, set resolved_at timestamp
        if self.status == 'resolved' and not self.resolved_at:
            from django.utils import timezone
            self.resolved_at = timezone.now()
            
        super(SupportTicket, self).save(*args, **kwargs)


class SupportTicketReply(models.Model):
    """Model to store replies to support tickets"""
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='replies')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    is_admin = models.BooleanField(default=False)  # To distinguish between admin and merchant replies
    message = models.TextField()
    attachment = models.FileField(upload_to='support_replies/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
        
    def __str__(self):
        return f"Reply to {self.ticket.ticket_id} by {'Admin' if self.is_admin else 'Merchant'}"


class SupportTicketNotification(models.Model):
    """Model to track notifications sent about support tickets"""
    NOTIFICATION_TYPE_CHOICES = [
        ('new_ticket', 'New Ticket'),
        ('ticket_reply', 'Ticket Reply'),
        ('status_change', 'Status Change'),
        ('assignment', 'Ticket Assignment'),
        ('escalation', 'Ticket Escalation'),
    ]
    
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPE_CHOICES)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='ticket_notifications')
    recipient_email = models.EmailField(null=True, blank=True)  # For notifications to non-users (e.g., merchant email)
    sent_at = models.DateTimeField(auto_now_add=True)
    delivered = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)
    template_used = models.CharField(max_length=100, null=True, blank=True)
    
    class Meta:
        ordering = ['-sent_at']
    
    def __str__(self):
        return f"{self.notification_type} notification for Ticket {self.ticket.ticket_id}"
