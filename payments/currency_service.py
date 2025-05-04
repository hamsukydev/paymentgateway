"""
Currency Service for Payment Gateway

This module handles currency conversion, exchange rates and formatting for the payment gateway.
It provides real-time exchange rates from external APIs and maintains a local cache.
"""

import os
import json
import requests
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Default cache times
RATES_CACHE_TIME = 60 * 60  # 1 hour in seconds
FALLBACK_RATES_VALID_TIME = 24 * 60 * 60  # 24 hours in seconds

# Supported currencies with expanded list
SUPPORTED_CURRENCIES = {
    'USD': {'symbol': '$', 'name': 'US Dollar', 'decimal_places': 2},
    'EUR': {'symbol': '€', 'name': 'Euro', 'decimal_places': 2},
    'GBP': {'symbol': '£', 'name': 'British Pound', 'decimal_places': 2},
    'NGN': {'symbol': '₦', 'name': 'Nigerian Naira', 'decimal_places': 2},
    'KES': {'symbol': 'KSh', 'name': 'Kenyan Shilling', 'decimal_places': 2},
    'ZAR': {'symbol': 'R', 'name': 'South African Rand', 'decimal_places': 2},
    'GHS': {'symbol': 'GH₵', 'name': 'Ghanaian Cedi', 'decimal_places': 2},
    'CAD': {'symbol': 'C$', 'name': 'Canadian Dollar', 'decimal_places': 2},
    'AUD': {'symbol': 'A$', 'name': 'Australian Dollar', 'decimal_places': 2},
    'JPY': {'symbol': '¥', 'name': 'Japanese Yen', 'decimal_places': 0},
    'INR': {'symbol': '₹', 'name': 'Indian Rupee', 'decimal_places': 2},
    'CNY': {'symbol': '¥', 'name': 'Chinese Yuan', 'decimal_places': 2},
    'AED': {'symbol': 'د.إ', 'name': 'UAE Dirham', 'decimal_places': 2},
    'EGP': {'symbol': 'E£', 'name': 'Egyptian Pound', 'decimal_places': 2},
    'UGX': {'symbol': 'USh', 'name': 'Ugandan Shilling', 'decimal_places': 0},
    'TZS': {'symbol': 'TSh', 'name': 'Tanzanian Shilling', 'decimal_places': 0},
    'RWF': {'symbol': 'RF', 'name': 'Rwandan Franc', 'decimal_places': 0},
    'BRL': {'symbol': 'R$', 'name': 'Brazilian Real', 'decimal_places': 2},
    'MXN': {'symbol': '$', 'name': 'Mexican Peso', 'decimal_places': 2},
    'SGD': {'symbol': 'S$', 'name': 'Singapore Dollar', 'decimal_places': 2},
    'XOF': {'symbol': 'CFA', 'name': 'West African CFA Franc', 'decimal_places': 0},
    'XAF': {'symbol': 'FCFA', 'name': 'Central African CFA Franc', 'decimal_places': 0},
}

# Exchange rate providers
EXCHANGE_RATE_PROVIDERS = [
    {
        'name': 'openexchangerates',
        'url': 'https://openexchangerates.org/api/latest.json',
        'params': {
            'app_id': getattr(settings, 'OPEN_EXCHANGE_API_KEY', ''),
            'base': 'USD',  # Free tier only supports USD as base
        },
        'parse_response': lambda resp: {
            curr.upper(): Decimal(str(rate))
            for curr, rate in resp.get('rates', {}).items()
        }
    },
    {
        'name': 'exchangerate-api',
        'url': 'https://v6.exchangerate-api.com/v6/{api_key}/latest/USD',
        'params': {},
        'parse_response': lambda resp: {
            curr.upper(): Decimal(str(rate))
            for curr, rate in resp.get('conversion_rates', {}).items()
        }
    },
    {
        'name': 'frankfurter',
        'url': 'https://api.frankfurter.app/latest',
        'params': {'from': 'USD'},
        'parse_response': lambda resp: {
            curr.upper(): Decimal(str(rate))
            for curr, rate in resp.get('rates', {}).items()
        }
    }
]

# Expanded fallback exchange rates in case all API calls fail
FALLBACK_RATES = {
    'USD': Decimal('1.0'),
    'EUR': Decimal('0.85'),
    'GBP': Decimal('0.73'),
    'NGN': Decimal('460.0'),
    'KES': Decimal('110.0'),
    'ZAR': Decimal('15.0'),
    'GHS': Decimal('10.0'),
    'CAD': Decimal('1.25'),
    'AUD': Decimal('1.30'),
    'JPY': Decimal('110.0'),
    'INR': Decimal('75.0'),
    'CNY': Decimal('6.5'),
    'AED': Decimal('3.67'),
    'EGP': Decimal('15.7'),
    'UGX': Decimal('3500.0'),
    'TZS': Decimal('2300.0'),
    'RWF': Decimal('1000.0'),
    'BRL': Decimal('5.0'),
    'MXN': Decimal('20.0'),
    'SGD': Decimal('1.35'),
    'XOF': Decimal('550.0'),
    'XAF': Decimal('550.0'),
}

class CurrencyService:
    """
    Service for handling currency-related operations
    """
    
    @staticmethod
    def get_exchange_rates(base_currency='USD', force_refresh=False):
        """
        Get current exchange rates for all supported currencies
        
        Args:
            base_currency: Base currency for rates (default: USD)
            force_refresh: If True, bypass cache and fetch new rates
            
        Returns:
            dict: Exchange rates with currency codes as keys
        """
        cache_key = f'exchange_rates_{base_currency}'
        
        # Try to get rates from cache first
        if not force_refresh:
            cached_rates = cache.get(cache_key)
            if cached_rates:
                logger.debug(f"Using cached exchange rates for {base_currency}")
                return cached_rates
        
        # If cache miss or forced refresh, fetch from API
        rates = None
        for provider in EXCHANGE_RATE_PROVIDERS:
            try:
                rates = CurrencyService._fetch_rates_from_provider(provider, base_currency)
                if rates:
                    # Store in cache
                    cache.set(cache_key, rates, RATES_CACHE_TIME)
                    logger.info(f"Updated exchange rates from {provider['name']}")
                    return rates
            except Exception as e:
                logger.warning(f"Failed to fetch rates from {provider['name']}: {str(e)}")
                continue
        
        # If all providers failed, use fallback rates
        logger.warning("All exchange rate providers failed, using fallback rates")
        return CurrencyService._get_fallback_rates(base_currency)
    
    @staticmethod
    def _fetch_rates_from_provider(provider, base_currency):
        """
        Fetch exchange rates from a specific provider
        
        Args:
            provider: Provider configuration dict
            base_currency: Base currency for rates
            
        Returns:
            dict: Exchange rates with currency codes as keys
        """
        url = provider['url']
        params = provider['params'].copy()
        
        # Some APIs embed the API key in the URL
        if '{api_key}' in url:
            url = url.format(api_key=getattr(settings, f"{provider['name'].upper().replace('-', '_')}_API_KEY", ''))
        
        # If provider supports changing base currency
        if 'base' in params:
            params['base'] = base_currency
        elif 'from' in params:
            params['from'] = base_currency
        
        # Make API request
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Parse rates using provider-specific function
        rates = provider['parse_response'](data)
        
        # If provider doesn't support the requested base currency (like free tier of some APIs)
        if base_currency != 'USD' and 'USD' in rates:
            # Convert all rates to requested base currency
            base_rate = rates.get(base_currency, Decimal('1.0'))
            if base_rate != Decimal('1.0'):
                return {
                    curr: rate / base_rate
                    for curr, rate in rates.items()
                }
                
        # Always make sure base currency rate is 1.0
        rates[base_currency] = Decimal('1.0')
        
        return rates
    
    @staticmethod
    def _get_fallback_rates(base_currency='USD'):
        """
        Get fallback exchange rates when API calls fail
        
        Args:
            base_currency: Base currency for rates (default: USD)
            
        Returns:
            dict: Fallback exchange rates
        """
        rates = FALLBACK_RATES.copy()
        
        # Always make sure base currency rate is 1.0
        if base_currency != 'USD':
            base_rate = rates.get(base_currency, Decimal('1.0'))
            rates = {curr: rate / base_rate for curr, rate in rates.items()}
        
        rates[base_currency] = Decimal('1.0')
        return rates
    
    @staticmethod
    def convert_amount(amount, from_currency, to_currency):
        """
        Convert an amount from one currency to another
        
        Args:
            amount: The amount to convert (Decimal, float or int)
            from_currency: Source currency code (e.g., 'USD')
            to_currency: Target currency code (e.g., 'EUR')
            
        Returns:
            Decimal: Converted amount in target currency
        """
        # No conversion needed for same currency
        if from_currency == to_currency:
            return Decimal(str(amount))
        
        # Get current rates
        rates = CurrencyService.get_exchange_rates(from_currency)
        
        # Perform conversion
        if to_currency in rates:
            converted = Decimal(str(amount)) * rates[to_currency]
            
            # Round to appropriate decimal places for target currency
            decimal_places = SUPPORTED_CURRENCIES.get(to_currency, {}).get('decimal_places', 2)
            return converted.quantize(Decimal('0.1') ** decimal_places)
        else:
            logger.error(f"Currency conversion failed: {to_currency} not in available rates")
            raise ValueError(f"Unsupported currency: {to_currency}")
    
    @staticmethod
    def format_amount(amount, currency):
        """
        Format an amount in the specified currency with proper symbol and formatting
        
        Args:
            amount: The amount to format (Decimal, float or int)
            currency: Currency code (e.g., 'USD')
            
        Returns:
            str: Formatted amount string (e.g., '$10.00')
        """
        # Convert to Decimal if not already
        if not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
        
        # Get currency details
        curr_info = SUPPORTED_CURRENCIES.get(currency, {
            'symbol': currency,
            'decimal_places': 2
        })
        
        # Round to appropriate decimal places
        decimal_places = curr_info.get('decimal_places', 2)
        rounded = amount.quantize(Decimal('0.1') ** decimal_places)
        
        # Format based on currency
        symbol = curr_info.get('symbol', '')
        
        # Handle different currency symbol positions
        if currency in ['USD', 'GBP', 'EUR', 'NGN', 'GHS', 'CAD', 'AUD', 'SGD', 'MXN', 'BRL']:
            # Symbol before amount
            return f"{symbol}{rounded:,.{decimal_places}f}"
        else:
            # Symbol after amount
            return f"{rounded:,.{decimal_places}f} {symbol}"
    
    @staticmethod
    def get_supported_currencies():
        """
        Get list of supported currencies with their details
        
        Returns:
            dict: Currency details dictionary
        """
        return SUPPORTED_CURRENCIES
    
    @staticmethod
    def is_currency_supported(currency_code):
        """
        Check if a currency is supported
        
        Args:
            currency_code: ISO currency code (e.g., 'USD')
            
        Returns:
            bool: True if currency is supported, False otherwise
        """
        return currency_code in SUPPORTED_CURRENCIES
    
    @staticmethod
    def sync_db_rates():
        """
        Synchronize current exchange rates to the database
        This ensures rates are persisted and can be used for historical reference
        """
        from .models import ExchangeRate
        
        # Get current USD-based rates
        rates = CurrencyService.get_exchange_rates('USD')
        
        # Update or create rates in database
        for currency, rate in rates.items():
            try:
                # Skip base currency
                if currency == 'USD':
                    continue
                    
                # Create or update rate
                exchange_rate, created = ExchangeRate.objects.update_or_create(
                    base_currency='USD',
                    target_currency=currency,
                    defaults={'rate': rate}
                )
                
                if created:
                    logger.info(f"Created new exchange rate: USD/{currency} = {rate}")
                else:
                    logger.debug(f"Updated exchange rate: USD/{currency} = {rate}")
                    
            except Exception as e:
                logger.error(f"Failed to update exchange rate for {currency}: {str(e)}")
        
        # Also create rates for other base currencies
        for base_currency in ['EUR', 'GBP', 'NGN']:
            if base_currency == 'USD':
                continue
                
            # Get rates for this base
            base_rates = CurrencyService.get_exchange_rates(base_currency)
            
            # Update popular currency pairs
            for target in ['USD', 'EUR', 'GBP', 'NGN']:
                if target == base_currency:
                    continue
                    
                try:
                    rate = base_rates.get(target)
                    if rate:
                        ExchangeRate.objects.update_or_create(
                            base_currency=base_currency,
                            target_currency=target,
                            defaults={'rate': rate}
                        )
                except Exception as e:
                    logger.error(f"Failed to update exchange rate for {base_currency}/{target}: {str(e)}")
    
    @staticmethod
    def get_merchant_currencies(merchant):
        """
        Get currencies supported by a specific merchant
        
        Args:
            merchant: Merchant object
            
        Returns:
            list: List of currency codes supported by the merchant
        """
        try:
            # Get merchant's supported currencies from database
            currencies = [mc.currency for mc in merchant.currencies.filter(is_active=True)]
            
            # If merchant has no specific currencies set up, return a default set
            if not currencies:
                return ['USD', 'EUR', 'GBP', 'NGN']
                
            return currencies
        except Exception as e:
            logger.error(f"Failed to get merchant currencies: {str(e)}")
            return ['USD', 'EUR', 'GBP', 'NGN']  # Default fallback
    
    @staticmethod
    def get_default_merchant_currency(merchant):
        """
        Get the default currency for a merchant
        
        Args:
            merchant: Merchant object
            
        Returns:
            str: Default currency code for the merchant
        """
        try:
            # Try to get merchant's default currency
            default_currency = merchant.currencies.filter(is_default=True).first()
            if default_currency:
                return default_currency.currency
            
            # If no default is set but merchant has currencies, use the first one
            first_currency = merchant.currencies.filter(is_active=True).first()
            if first_currency:
                return first_currency.currency
                
            # Fallback to the system default
            return 'NGN'
        except Exception as e:
            logger.error(f"Failed to get merchant default currency: {str(e)}")
            return 'NGN'  # Default fallback