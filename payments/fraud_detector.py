"""
Fraud Detection Service for Payment Gateway

This module provides fraud detection capabilities for transactions processed
through the payment gateway. It analyzes transaction patterns, IP addresses,
device fingerprints, and other signals to flag potentially fraudulent activity.
"""

import logging
import ipaddress
import datetime
import hashlib
import re
from django.utils import timezone
from django.db.models import Count, Sum, Avg, F, ExpressionWrapper, FloatField, Max
from django.db.models.functions import TruncDay, TruncHour

logger = logging.getLogger(__name__)

# Configure risk thresholds
RISK_THRESHOLDS = {
    'velocity': {
        'tx_per_hour': 10,        # Flag if more than 10 transactions per hour
        'tx_per_day': 30,         # Flag if more than 30 transactions per day
        'total_amount_per_day': 100000  # Flag if more than N amount per day (in smallest currency unit)
    },
    'location': {
        'different_countries_hours': 2,  # Flag if transactions from different countries within 2 hours
        'high_risk_countries': ['NGA', 'GHA', 'KEN', 'ZAF', 'UGA', 'TZA', 'GIN', 'CMR'],  # High-risk countries
    },
    'device': {
        'multiple_accounts': 3,    # Flag if same device used for more than 3 accounts in 24 hours
        'browser_anomalies': True  # Check for browser/device fingerprint anomalies
    },
    'payment': {
        'different_cards_hours': 2,  # Flag if more than 2 different cards within 1 hour
        'different_cards_day': 5,    # Flag if more than 5 different cards within 24 hours
        'bin_check': True            # Enable BIN validation check
    },
    'amount': {
        'max_customer_multiple': 5,  # Flag if amount is more than 5x customer average
        'max_merchant_multiple': 10  # Flag if amount is more than 10x merchant average
    },
    'ml_model': {
        'threshold': 0.85,           # Score above this is considered high risk
        'enabled': True              # Enable/disable machine learning model
    }
}

# High risk BIN ranges (first 6 digits of card)
HIGH_RISK_BINS = [
    # These would be populated based on fraud patterns
    '4***11', '5***22', '3***33'
]

# Suspicious email domains
SUSPICIOUS_EMAIL_DOMAINS = [
    'tempmail.com', 'guerrillamail.com', 'mailinator.com', 'yopmail.com',
    'trashmail.com', 'sharklasers.com'
]

# IP Blacklist - would be loaded from a database or external service in production
IP_BLACKLIST = set([
    # '123.456.789.012'
])

def analyze_transaction(transaction, ip=None, device_fingerprint=None):
    """
    Main entry point for fraud analysis.
    Analyzes a transaction and updates its risk score and flags.
    
    Args:
        transaction: The Transaction object to analyze
        ip: IP address that initiated the transaction (optional)
        device_fingerprint: Browser/device fingerprint (optional)
    
    Returns:
        tuple: (risk_level, risk_score, risk_factors)
    """
    risk_factors = []
    risk_score = 0
    
    # Skip analysis for whitelisted customers or merchants if needed
    if is_whitelisted(transaction):
        transaction.risk_score = 0
        transaction.set_risk_flags({})
        transaction.save(update_fields=['risk_score', 'metadata'])
        return "low", 0, []
    
    # Add timestamp of analysis
    analysis_time = timezone.now()
    
    # Check for blacklisted IP
    if ip and ip in IP_BLACKLIST:
        risk_score = 100
        risk_factors.append("Blacklisted IP address")
        
        transaction.risk_score = risk_score
        transaction.set_risk_flags({
            'level': "high",
            'factors': risk_factors,
            'score': risk_score,
            'analyzed_at': analysis_time.isoformat()
        })
        transaction.save(update_fields=['risk_score', 'metadata'])
        
        logger.warning(f"Blacklisted IP detected for transaction: {transaction.reference}, IP: {ip}")
        return "high", risk_score, risk_factors
    
    # 1. Check velocity patterns
    velocity_risk, velocity_factors = check_velocity_patterns(transaction)
    risk_factors.extend(velocity_factors)
    risk_score += velocity_risk * 25  # Velocity issues contribute up to 25 points
    
    # 2. Location analysis (if IP provided)
    if ip:
        location_risk, location_factors = analyze_ip_location(transaction, ip)
        risk_factors.extend(location_factors)
        risk_score += location_risk * 20  # Location issues contribute up to 20 points
    
    # 3. Device fingerprint analysis (if provided)
    if device_fingerprint:
        device_risk, device_factors = analyze_device(transaction, device_fingerprint)
        risk_factors.extend(device_factors)
        risk_score += device_risk * 15  # Device issues contribute up to 15 points
    
    # 4. Payment method analysis
    payment_risk, payment_factors = analyze_payment_method(transaction)
    risk_factors.extend(payment_factors)
    risk_score += payment_risk * 20  # Payment method issues contribute up to 20 points
    
    # 5. Amount analysis
    amount_risk, amount_factors = analyze_amount(transaction)
    risk_factors.extend(amount_factors)
    risk_score += amount_risk * 20  # Amount issues contribute up to 20 points
    
    # 6. Email analysis
    email_risk, email_factors = analyze_email(transaction)
    risk_factors.extend(email_factors)
    risk_score += email_risk * 10  # Email issues contribute up to 10 points
    
    # 7. Machine learning risk model (if enabled)
    if RISK_THRESHOLDS['ml_model']['enabled']:
        ml_risk, ml_factors = analyze_with_ml_model(transaction, ip, device_fingerprint)
        risk_factors.extend(ml_factors)
        risk_score += ml_risk * 30  # ML model contributes up to 30 points
    
    # Cap risk score at 100
    risk_score = min(risk_score, 100)
    
    # Determine risk level
    risk_level = "low"
    if risk_score >= 80:
        risk_level = "high"
    elif risk_score >= 50:
        risk_level = "medium"
    
    # Save risk assessment to transaction
    transaction.risk_score = risk_score
    transaction.set_risk_flags({
        'level': risk_level,
        'factors': risk_factors,
        'score': risk_score,
        'analyzed_at': analysis_time.isoformat()
    })
    transaction.save(update_fields=['risk_score', 'metadata'])
    
    # Log high-risk transactions
    if risk_level in ["medium", "high"]:
        logger.warning(
            f"High risk transaction detected: {transaction.reference}, "
            f"Score: {risk_score}, Factors: {', '.join(risk_factors)}"
        )
    
    return risk_level, risk_score, risk_factors

def is_whitelisted(transaction):
    """
    Check if a transaction is from a whitelisted customer or merchant
    """
    if not transaction.customer:
        return False
    
    # Check customer metadata for whitelist flag
    customer_metadata = transaction.customer.get_metadata() or {}
    if customer_metadata.get('whitelisted'):
        return True
    
    # Check merchant settings for VIP customers
    merchant_metadata = transaction.merchant.get_metadata() or {}
    vip_customers = merchant_metadata.get('vip_customers', [])
    if transaction.customer.email in vip_customers:
        return True
    
    return False

def check_velocity_patterns(transaction):
    """
    Check for suspicious transaction velocity patterns.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    from .models import Transaction
    
    risk_factor = 0
    risk_descriptions = []
    customer = transaction.customer
    
    # Only check for repeat customers
    if not customer:
        return 0, []
    
    now = timezone.now()
    one_hour_ago = now - datetime.timedelta(hours=1)
    one_day_ago = now - datetime.timedelta(days=1)
    
    # Check transactions per hour
    tx_count_hour = Transaction.objects.filter(
        customer=customer,
        created_at__gte=one_hour_ago
    ).count()
    
    if tx_count_hour >= RISK_THRESHOLDS['velocity']['tx_per_hour']:
        risk_factor += 0.5
        risk_descriptions.append(f"High transaction velocity: {tx_count_hour} transactions in 1 hour")
    
    # Check transactions per day
    tx_count_day = Transaction.objects.filter(
        customer=customer,
        created_at__gte=one_day_ago
    ).count()
    
    if tx_count_day >= RISK_THRESHOLDS['velocity']['tx_per_day']:
        risk_factor += 0.5
        risk_descriptions.append(f"High transaction velocity: {tx_count_day} transactions in 24 hours")
    
    # Check total amount per day
    if tx_count_day > 1:  # Only if there were previous transactions today
        total_amount = Transaction.objects.filter(
            customer=customer,
            created_at__gte=one_day_ago,
            currency=transaction.currency  # Only same currency
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Add current transaction amount
        total_amount += float(transaction.amount)
        
        if total_amount >= RISK_THRESHOLDS['velocity']['total_amount_per_day']:
            risk_factor += 0.5
            formatted_amount = format_currency(total_amount, transaction.currency)
            risk_descriptions.append(f"High transaction volume: {formatted_amount} in 24 hours")
    
    return min(risk_factor, 1.0), risk_descriptions

def analyze_ip_location(transaction, ip_address):
    """
    Analyze IP address for location-based fraud signals.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    from .models import Transaction
    
    risk_factor = 0
    risk_descriptions = []
    customer = transaction.customer
    
    if not ip_address:
        return 0, []
    
    # Check if IP is in a high-risk country
    try:
        ip_network = ipaddress.ip_address(ip_address)
        
        # Here you would typically use a geolocation service to get country
        # For this example, we'll assume a function that returns country code
        country_code = get_country_from_ip(ip_address)
        
        # Check if country is in high risk list
        if country_code and country_code in RISK_THRESHOLDS['location']['high_risk_countries']:
            risk_factor += 0.4
            risk_descriptions.append(f"Transaction from high-risk country: {country_code}")
        
        # For demo purposes, we'll just check if it's a private IP
        if ip_network.is_private:
            # Local testing, no risk
            pass
        elif ip_network.is_global:
            # Could check against high-risk countries list
            pass
    except ValueError:
        # Invalid IP format
        risk_factor += 0.3
        risk_descriptions.append("Invalid IP address format")
    
    # Check if customer has transactions from different countries recently
    if customer:
        threshold_hours = RISK_THRESHOLDS['location']['different_countries_hours']
        recent_time = timezone.now() - datetime.timedelta(hours=threshold_hours)
        
        # In practice, you'd aggregate by country
        # recent_countries = Transaction.objects.filter(
        #    customer=customer,
        #    created_at__gte=recent_time
        # ).values('ip_country').distinct().count()
        
        # For this example, we'll just check if there's a stored IP
        # that doesn't match the current one
        recent_transactions = Transaction.objects.filter(
            customer=customer,
            created_at__gte=recent_time
        ).exclude(id=transaction.id)[:5]
        
        distinct_ips = set()
        for tx in recent_transactions:
            metadata = tx.get_metadata() or {}
            if 'ip_address' in metadata and metadata['ip_address'] != ip_address:
                distinct_ips.add(metadata['ip_address'])
        
        if len(distinct_ips) > 0:
            risk_factor += 0.5
            risk_descriptions.append(f"Different IP addresses used within {threshold_hours} hours")
    
    # Check for impossible travel (location changes too quickly)
    if customer:
        previous_tx = Transaction.objects.filter(
            customer=customer
        ).exclude(id=transaction.id).order_by('-created_at').first()
        
        if previous_tx:
            previous_tx_metadata = previous_tx.get_metadata() or {}
            prev_ip = previous_tx_metadata.get('ip_address')
            
            if prev_ip and prev_ip != ip_address:
                # Get locations
                prev_country = get_country_from_ip(prev_ip)
                current_country = get_country_from_ip(ip_address)
                
                if prev_country and current_country and prev_country != current_country:
                    # Calculate time difference
                    time_diff = timezone.now() - previous_tx.created_at
                    hours_diff = time_diff.total_seconds() / 3600
                    
                    # If less than 2 hours between transactions in different countries
                    if hours_diff < 2:
                        risk_factor += 0.8
                        risk_descriptions.append(f"Impossible travel: {prev_country} to {current_country} in {hours_diff:.1f} hours")
    
    # Store IP in transaction metadata
    metadata = transaction.get_metadata() or {}
    metadata['ip_address'] = ip_address
    transaction.set_metadata(metadata)
    
    return min(risk_factor, 1.0), risk_descriptions

def analyze_device(transaction, device_fingerprint):
    """
    Analyze device fingerprint for fraud signals.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    from .models import Transaction, Customer
    
    risk_factor = 0
    risk_descriptions = []
    
    if not device_fingerprint:
        return 0, []
    
    # Store fingerprint in transaction metadata
    metadata = transaction.get_metadata() or {}
    metadata['device_fingerprint'] = device_fingerprint
    transaction.set_metadata(metadata)
    
    # Find recent transactions with same device fingerprint but different customers
    one_day_ago = timezone.now() - datetime.timedelta(days=1)
    
    # This would require a JSON lookup in a real DB, which varies by DB engine
    # For PostgreSQL: Transaction.objects.filter(metadata__contains={'device_fingerprint': device_fingerprint})
    # Simplified version:
    recent_transactions = Transaction.objects.filter(
        created_at__gte=one_day_ago
    ).exclude(id=transaction.id)[:100]  # Limit search to recent transactions
    
    matching_device_customers = set()
    for tx in recent_transactions:
        tx_metadata = tx.get_metadata() or {}
        if tx_metadata.get('device_fingerprint') == device_fingerprint and tx.customer:
            if transaction.customer and tx.customer.id != transaction.customer.id:
                matching_device_customers.add(tx.customer.id)
    
    if len(matching_device_customers) >= RISK_THRESHOLDS['device']['multiple_accounts']:
        risk_factor = 1.0
        risk_descriptions.append(f"Device used by {len(matching_device_customers)+1} different customers in 24 hours")
    
    # Check if fingerprint has been modified
    if RISK_THRESHOLDS['device']['browser_anomalies'] and 'user_agent' in metadata:
        user_agent = metadata.get('user_agent', '')
        browser_anomalies = detect_browser_anomalies(user_agent, device_fingerprint)
        
        if browser_anomalies:
            risk_factor += 0.5
            risk_descriptions.append("Browser fingerprint anomalies detected")
    
    return risk_factor, risk_descriptions

def analyze_payment_method(transaction):
    """
    Analyze payment method for fraud signals.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    from .models import Transaction
    
    risk_factor = 0
    risk_descriptions = []
    customer = transaction.customer
    
    if not customer or not transaction.payment_method:
        return 0, []
    
    # Check for multiple cards in short time periods
    metadata = transaction.get_metadata() or {}
    current_card = None
    
    if transaction.payment_method == 'card' and 'card' in metadata:
        card_info = metadata.get('card', {})
        if 'last4' in card_info and 'bin' in card_info:
            current_card = f"{card_info['bin']}...{card_info['last4']}"
            
            # Check if BIN is in high-risk list
            if RISK_THRESHOLDS['payment']['bin_check']:
                bin_number = card_info.get('bin', '')
                if bin_number and is_high_risk_bin(bin_number):
                    risk_factor += 0.6
                    risk_descriptions.append("Card from high-risk BIN range")
    
    if current_card:
        # Check cards used in last hour
        one_hour_ago = timezone.now() - datetime.timedelta(hours=1)
        
        recent_transactions = Transaction.objects.filter(
            customer=customer,
            created_at__gte=one_hour_ago,
            payment_method='card'
        ).exclude(id=transaction.id)
        
        distinct_cards = set()
        for tx in recent_transactions:
            tx_metadata = tx.get_metadata() or {}
            if 'card' in tx_metadata:
                card_info = tx_metadata['card']
                if 'last4' in card_info and 'bin' in card_info:
                    card_id = f"{card_info['bin']}...{card_info['last4']}"
                    if card_id != current_card:
                        distinct_cards.add(card_id)
        
        if len(distinct_cards) >= RISK_THRESHOLDS['payment']['different_cards_hours']:
            risk_factor += 0.7
            risk_descriptions.append(f"Used {len(distinct_cards)+1} different cards within an hour")
        
        # Check cards used in last day
        one_day_ago = timezone.now() - datetime.timedelta(days=1)
        
        daily_transactions = Transaction.objects.filter(
            customer=customer,
            created_at__gte=one_day_ago,
            payment_method='card'
        ).exclude(id=transaction.id)
        
        daily_distinct_cards = set()
        for tx in daily_transactions:
            tx_metadata = tx.get_metadata() or {}
            if 'card' in tx_metadata:
                card_info = tx_metadata['card']
                if 'last4' in card_info and 'bin' in card_info:
                    card_id = f"{card_info['bin']}...{card_info['last4']}"
                    if card_id != current_card:
                        daily_distinct_cards.add(card_id)
        
        if len(daily_distinct_cards) >= RISK_THRESHOLDS['payment']['different_cards_day']:
            risk_factor += 0.7
            risk_descriptions.append(f"Used {len(daily_distinct_cards)+1} different cards within 24 hours")
    
    return min(risk_factor, 1.0), risk_descriptions

def analyze_amount(transaction):
    """
    Analyze transaction amount for fraud signals.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    from .models import Transaction
    
    risk_factor = 0
    risk_descriptions = []
    customer = transaction.customer
    amount = float(transaction.amount)
    
    # Check for suspicious round amounts
    if amount > 1000 and amount % 1000 == 0:
        risk_factor += 0.2
        risk_descriptions.append(f"Suspiciously round amount: {format_currency(amount, transaction.currency)}")
    
    # Check against customer history
    if customer:
        # Get customer's average transaction amount
        customer_avg = Transaction.objects.filter(
            customer=customer,
            currency=transaction.currency,
            status='success'
        ).exclude(id=transaction.id).aggregate(avg=Avg('amount'))['avg']
        
        # If customer has previous transactions, check if this one is much larger
        if customer_avg is not None:
            # Calculate how many times larger this transaction is
            if customer_avg > 0:  # Avoid division by zero
                times_larger = amount / customer_avg
                
                if times_larger > RISK_THRESHOLDS['amount']['max_customer_multiple']:
                    risk_factor += 0.5
                    risk_descriptions.append(f"Amount {times_larger:.1f}x larger than customer average")
        
        # Get customer's max transaction amount
        customer_max = Transaction.objects.filter(
            customer=customer,
            currency=transaction.currency,
            status='success'
        ).exclude(id=transaction.id).aggregate(max_amount=Max('amount'))['max_amount']
        
        if customer_max is not None and amount > customer_max * 2:
            risk_factor += 0.3
            risk_descriptions.append(f"Amount {amount / customer_max:.1f}x larger than customer's previous maximum")
    
    # Get merchant's average transaction amount
    merchant_avg = Transaction.objects.filter(
        merchant=transaction.merchant,
        currency=transaction.currency,
        status='success'
    ).aggregate(avg=Avg('amount'))['avg']
    
    # If there are other transactions for this merchant, check if this one is much larger
    if merchant_avg is not None:
        # Calculate how many times larger this transaction is
        if merchant_avg > 0:  # Avoid division by zero
            times_larger = amount / merchant_avg
            
            if times_larger > RISK_THRESHOLDS['amount']['max_merchant_multiple']:
                risk_factor += 0.3
                risk_descriptions.append(f"Amount {times_larger:.1f}x larger than merchant average")
    
    return min(risk_factor, 1.0), risk_descriptions

def analyze_email(transaction):
    """
    Analyze email for fraud signals.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    risk_factor = 0
    risk_descriptions = []
    
    if not transaction.email:
        return 0, []
        
    email = transaction.email.lower()
    
    # Check for disposable email services
    domain = email.split('@')[-1]
    if domain in SUSPICIOUS_EMAIL_DOMAINS:
        risk_factor += 0.7
        risk_descriptions.append(f"Disposable email address detected: {domain}")
    
    # Check if email was recently created
    if transaction.customer:
        customer_created = transaction.customer.created_at
        time_diff = timezone.now() - customer_created
        hours_diff = time_diff.total_seconds() / 3600
        
        if hours_diff < 1:
            risk_factor += 0.4
            risk_descriptions.append("Account created less than 1 hour before transaction")
        elif hours_diff < 24:
            risk_factor += 0.2
            risk_descriptions.append("Account created less than 24 hours before transaction")
    
    # Check for nonsensical or auto-generated email patterns
    if re.match(r'^[a-z0-9]{10,}@', email) or re.match(r'^[a-z0-9]+\d{4,}@', email):
        risk_factor += 0.3
        risk_descriptions.append("Suspicious email pattern detected")
    
    return min(risk_factor, 1.0), risk_descriptions

def analyze_with_ml_model(transaction, ip=None, device_fingerprint=None):
    """
    Use machine learning model to analyze fraud risk.
    
    Returns:
        tuple: (risk_factor, list_of_risk_descriptions)
    """
    # This would call an external ML service or use a local model
    # For this example, we'll simulate a model response
    
    risk_factor = 0
    risk_descriptions = []
    
    try:
        # Build features for the model
        features = {
            'amount': float(transaction.amount),
            'currency': transaction.currency,
            'payment_method': transaction.payment_method,
            'customer_id': transaction.customer.id if transaction.customer else None,
            'merchant_id': transaction.merchant.id,
            'ip_address': ip,
            'device_fingerprint': device_fingerprint,
            'hour_of_day': transaction.created_at.hour,
            'day_of_week': transaction.created_at.weekday(),
        }
        
        # Add metadata features if available
        metadata = transaction.get_metadata() or {}
        if 'browser' in metadata:
            features['browser'] = metadata['browser']
        if 'os' in metadata:
            features['os'] = metadata['os']
        
        # In a real implementation, you would:
        # 1. Call an ML model API
        # model_result = ml_fraud_service.predict(features)
        
        # 2. Extract the risk score and reasons
        # risk_score = model_result.get('score', 0)
        # risk_reasons = model_result.get('reasons', [])
        
        # For this example, we'll simulate it
        # Placeholder for ML model integration
        risk_score = simulate_ml_fraud_score(features)
        
        if risk_score >= RISK_THRESHOLDS['ml_model']['threshold']:
            risk_factor = 1.0
            risk_descriptions.append(f"ML model flagged as high risk: {risk_score:.2f}")
        elif risk_score >= RISK_THRESHOLDS['ml_model']['threshold'] * 0.7:
            risk_factor = 0.5
            risk_descriptions.append(f"ML model flagged as medium risk: {risk_score:.2f}")
        
        # Store ML score in metadata
        metadata = transaction.get_metadata() or {}
        if 'ml_scores' not in metadata:
            metadata['ml_scores'] = []
        
        metadata['ml_scores'].append({
            'timestamp': timezone.now().isoformat(),
            'score': risk_score
        })
        
        transaction.set_metadata(metadata)
        
    except Exception as e:
        logger.error(f"Error in ML fraud analysis: {str(e)}")
        
    return min(risk_factor, 1.0), risk_descriptions

def get_country_from_ip(ip_address):
    """
    Get country code from IP address.
    In production, this would use a geolocation service.
    
    Returns:
        str: Two-letter country code or None
    """
    # In a real implementation, you would use a geolocation service
    # For demo purposes, we'll return None or fake codes
    if not ip_address:
        return None
        
    # Simple demo logic
    try:
        ip = ipaddress.ip_address(ip_address)
        if ip.is_private:
            return None
            
        # Fake mapping for demo purposes
        if str(ip).startswith('1.'):
            return 'US'
        elif str(ip).startswith('2.'):
            return 'GB'
        elif str(ip).startswith('41.'):
            return 'NGA'  # Nigeria
        else:
            return None
            
    except ValueError:
        return None

def is_high_risk_bin(bin_number):
    """
    Check if a card BIN is high risk.
    
    Args:
        bin_number: First 6 digits of card
        
    Returns:
        bool: True if high risk
    """
    if not bin_number or len(bin_number) < 6:
        return False
        
    bin_prefix = bin_number[:6]
    
    for pattern in HIGH_RISK_BINS:
        if match_bin_pattern(bin_prefix, pattern):
            return True
            
    return False

def match_bin_pattern(bin_number, pattern):
    """
    Match BIN against pattern with wildcards.
    
    Args:
        bin_number: Actual BIN number
        pattern: Pattern with * as wildcards
        
    Returns:
        bool: True if matches
    """
    if len(bin_number) != len(pattern):
        return False
        
    for i, ch in enumerate(pattern):
        if ch != '*' and ch != bin_number[i]:
            return False
            
    return True

def detect_browser_anomalies(user_agent, fingerprint):
    """
    Detect anomalies in browser fingerprint.
    
    Returns:
        bool: True if anomalies detected
    """
    # In a real implementation, this would check for:
    # - Mismatched OS/browser info in user agent vs. fingerprint
    # - Unusual screen resolution for claimed device
    # - Browser plugins inconsistent with OS
    # - Timezone inconsistencies
    # - Canvas/WebGL fingerprinting anomalies
    
    # For this example, we'll return False (no anomalies)
    return False

def simulate_ml_fraud_score(features):
    """
    Simulate an ML model fraud score.
    
    Args:
        features: Transaction features
        
    Returns:
        float: Risk score between 0 and 1
    """
    # Simple heuristic for demonstration
    # In production, this would be a proper trained model
    
    base_score = 0.1  # Start with low risk
    
    # Add risk based on payment method
    if features.get('payment_method') == 'card':
        base_score += 0.05
    
    # Add risk for high amount
    amount = features.get('amount', 0)
    if amount > 10000:
        base_score += 0.2
    elif amount > 5000:
        base_score += 0.1
        
    # Add risk for night-time transactions
    hour = features.get('hour_of_day', 0)
    if 1 <= hour <= 5:  # Between 1 AM and 5 AM
        base_score += 0.15
    
    # Hash the combined features to get a pseudo-random component
    feature_str = str(features)
    hash_val = int(hashlib.md5(feature_str.encode()).hexdigest(), 16)
    random_component = (hash_val % 1000) / 1000  # Random value between 0 and 1
    
    # Combine base score with random component
    final_score = min(0.95, base_score + (random_component * 0.3))
    
    return final_score

def format_currency(amount, currency):
    """Format currency amount for display"""
    if currency == 'USD':
        return f"${amount:.2f}"
    elif currency == 'EUR':
        return f"€{amount:.2f}"
    elif currency == 'GBP':
        return f"£{amount:.2f}"
    elif currency == 'NGN':
        return f"₦{amount:.2f}"
    else:
        return f"{amount:.2f} {currency}"