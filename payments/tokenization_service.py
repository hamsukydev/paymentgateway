"""
Tokenization Service

This module handles secure tokenization of payment methods like credit cards, enabling
recurring payments without storing sensitive card data directly in the database.
"""

import os
import base64
import hashlib
import hmac
import uuid
import json
from django.conf import settings
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

logger = logging.getLogger(__name__)

class TokenizationService:
    """
    Service for tokenizing and detokenizing payment methods securely.
    """
    
    # Encryption key settings
    ENCRYPTION_KEY = getattr(settings, 'TOKENIZATION_KEY', None)
    SALT = getattr(settings, 'TOKENIZATION_SALT', b'hamsukypay_tokenization_salt')
    
    @classmethod
    def _get_encryption_key(cls):
        """
        Get or generate the encryption key
        """
        if cls.ENCRYPTION_KEY:
            return cls.ENCRYPTION_KEY
        
        # Generate a key from the Django secret key
        secret_key = settings.SECRET_KEY.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=cls.SALT,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret_key))
        return key
    
    @classmethod
    def tokenize_card(cls, card_details):
        """
        Tokenize card details into a secure token
        
        Args:
            card_details (dict): Dictionary containing card details
                - number: Card number
                - expiry_month: Expiry month
                - expiry_year: Expiry year
                - cvv: CVV (will be discarded, not stored)
                - cardholder_name: Optional cardholder name
        
        Returns:
            tuple: (token, masked_details) where token is a string and 
                   masked_details is a dict with non-sensitive data
        """
        try:
            # Validate input
            required_fields = ['number', 'expiry_month', 'expiry_year']
            for field in required_fields:
                if field not in card_details:
                    raise ValueError(f"Missing required field: {field}")
            
            # Extract and clean card details
            card_number = str(card_details['number']).replace(' ', '')
            expiry_month = str(card_details['expiry_month']).zfill(2)
            expiry_year = str(card_details['expiry_year'])
            if len(expiry_year) == 2:
                expiry_year = f"20{expiry_year}"
            
            # Create a data payload for encryption
            # Note: We do NOT store the CVV, even encrypted
            payload = {
                'card_number': card_number,
                'expiry_month': expiry_month,
                'expiry_year': expiry_year,
                'cardholder_name': card_details.get('cardholder_name', ''),
                'created_at': str(uuid.uuid4())  # Add a unique component for entropy
            }
            
            # Convert to JSON string
            payload_json = json.dumps(payload)
            
            # Encrypt the payload
            key = cls._get_encryption_key()
            fernet = Fernet(key)
            encrypted_payload = fernet.encrypt(payload_json.encode())
            
            # Create token with version prefix
            token = f"tok_1_{base64.urlsafe_b64encode(encrypted_payload).decode()}"
            
            # Create masked card details safe for storage
            masked_details = {
                'last4': card_number[-4:],
                'first6': card_number[:6],
                'expiry_month': expiry_month,
                'expiry_year': expiry_year,
                'token': token
            }
            
            return token, masked_details
            
        except Exception as e:
            logger.error(f"Tokenization failed: {str(e)}")
            raise
    
    @classmethod
    def detokenize_card(cls, token):
        """
        Convert a token back into card details
        
        Args:
            token (str): The tokenized card string
        
        Returns:
            dict: Card details (without CVV)
        """
        try:
            if not token or not isinstance(token, str):
                raise ValueError("Invalid token")
            
            # Check token version
            if not token.startswith('tok_1_'):
                raise ValueError("Unsupported token version")
            
            # Extract encrypted payload
            encrypted_payload = base64.urlsafe_b64decode(token[6:])
            
            # Decrypt the payload
            key = cls._get_encryption_key()
            fernet = Fernet(key)
            decrypted_payload = fernet.decrypt(encrypted_payload)
            
            # Parse the JSON payload
            card_details = json.loads(decrypted_payload.decode())
            
            # Remove the unique component added during tokenization
            card_details.pop('created_at', None)
            
            return card_details
            
        except Exception as e:
            logger.error(f"Detokenization failed: {str(e)}")
            raise
    
    @classmethod
    def validate_token(cls, token):
        """
        Validate if a token is properly formatted and can be decrypted
        
        Args:
            token (str): The token to validate
        
        Returns:
            bool: True if the token is valid, False otherwise
        """
        try:
            cls.detokenize_card(token)
            return True
        except:
            return False
    
    @classmethod
    def generate_fingerprint(cls, card_number):
        """
        Generate a card fingerprint for duplicate detection
        
        Args:
            card_number (str): The card number
        
        Returns:
            str: A fingerprint hash that can identify the same card across transactions
        """
        try:
            # Clean the card number
            card_number = str(card_number).replace(' ', '')
            
            # Use a specific key for fingerprinting
            key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            
            # Create HMAC
            fingerprint = hmac.new(
                key,
                card_number.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return fingerprint
            
        except Exception as e:
            logger.error(f"Fingerprint generation failed: {str(e)}")
            raise
    
    @classmethod
    def get_card_type(cls, card_number):
        """
        Determine card type from card number
        
        Args:
            card_number (str): Card number
        
        Returns:
            str: Card type (visa, mastercard, etc.)
        """
        card_number = str(card_number).replace(' ', '')
        
        # Common card type patterns
        card_patterns = {
            'visa': r'^4[0-9]{12}(?:[0-9]{3})?$',
            'mastercard': r'^5[1-5][0-9]{14}$|^2(?:2(?:2[1-9]|[3-9][0-9])|[3-6][0-9][0-9]|7(?:[01][0-9]|20))[0-9]{12}$',
            'amex': r'^3[47][0-9]{13}$',
            'discover': r'^6(?:011|5[0-9]{2})[0-9]{12}$',
            'diners': r'^3(?:0[0-5]|[68][0-9])[0-9]{11}$',
            'jcb': r'^(?:2131|1800|35[0-9]{3})[0-9]{11}$',
            'verve': r'^506[0-1][0-9]{10}$|^507[0-9]{10}$|^6500[0-9]{10}$',
        }
        
        import re
        for card_type, pattern in card_patterns.items():
            if re.match(pattern, card_number):
                return card_type
        
        # Check prefixes for other card types
        if card_number.startswith('62'):
            return 'unionpay'
        elif card_number.startswith('5019') or card_number.startswith('4571'):
            return 'dankort'
        
        return 'unknown'