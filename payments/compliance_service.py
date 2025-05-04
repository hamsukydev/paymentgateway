import logging
import re
import json
import hashlib
import datetime
from decimal import Decimal
from typing import Dict, List, Tuple, Optional, Any, Union

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

# Import models when we implement the service in the Django project
# from .models import Transaction, Customer, ComplianceLog, MerchantSettings, RiskAssessment

logger = logging.getLogger(__name__)

class ComplianceService:
    """Main service class for handling all compliance-related operations"""
    
    # Constants for risk thresholds
    LOW_RISK_THRESHOLD = 0.3
    MEDIUM_RISK_THRESHOLD = 0.7
    HIGH_RISK_THRESHOLD = 0.9
    
    # PEP and Sanctions lists cache key
    PEP_CACHE_KEY = "compliance_pep_list"
    SANCTIONS_CACHE_KEY = "compliance_sanctions_list"
    
    # Cache timeout (24 hours)
    CACHE_TIMEOUT = 86400
    
    @classmethod
    def evaluate_transaction(cls, transaction):
        """
        Main entry point for transaction compliance evaluation
        Returns a tuple of (is_compliant, risk_score, actions_required, reasons)
        """
        # Initialize compliance checks
        pci_compliant = cls.check_pci_compliance(transaction)
        aml_status, aml_risk, aml_actions, aml_reasons = cls.perform_aml_check(transaction)
        kyc_status, kyc_risk, kyc_actions, kyc_reasons = cls.check_kyc_requirements(transaction)
        
        # Combine risk scores (weighted average)
        pci_weight = 0.3
        aml_weight = 0.4
        kyc_weight = 0.3
        
        overall_risk = (
            (1.0 if not pci_compliant else 0.0) * pci_weight + 
            aml_risk * aml_weight + 
            kyc_risk * kyc_weight
        )
        
        # Determine compliance status
        is_compliant = pci_compliant and aml_status and kyc_status
        
        # Combine required actions and reasons
        actions_required = []
        if not pci_compliant:
            actions_required.append("pci_compliance_required")
        actions_required.extend(aml_actions)
        actions_required.extend(kyc_actions)
        
        reasons = []
        if not pci_compliant:
            reasons.append("PCI-DSS compliance requirements not met")
        reasons.extend(aml_reasons)
        reasons.extend(kyc_reasons)
        
        # Log the compliance check
        cls._log_compliance_check(
            transaction, 
            is_compliant,
            overall_risk,
            {
                "pci_compliance": pci_compliant,
                "aml_status": aml_status,
                "kyc_status": kyc_status,
                "reasons": reasons,
                "actions_required": actions_required
            }
        )
        
        # Update transaction with compliance status if we're using Django models
        try:
            transaction.risk_score = overall_risk
            transaction.pci_compliant = pci_compliant
            transaction.aml_cleared = aml_status
            transaction.kyc_verified = kyc_status
            
            if is_compliant:
                transaction.compliance_status = "approved"
            elif overall_risk > cls.HIGH_RISK_THRESHOLD:
                transaction.compliance_status = "rejected"
            else:
                transaction.compliance_status = "review"
                
            transaction.save(update_fields=[
                'risk_score', 'pci_compliant', 'aml_cleared', 
                'kyc_verified', 'compliance_status'
            ])
        except Exception as e:
            logger.error(f"Failed to update transaction compliance status: {e}")
        
        return is_compliant, overall_risk, actions_required, reasons

    @classmethod
    def check_pci_compliance(cls, transaction) -> bool:
        """
        Checks if the transaction meets PCI-DSS compliance requirements.
        
        PCI-DSS (Payment Card Industry Data Security Standard) requirements include:
        1. Never storing full credit card numbers
        2. Encrypting transmitted card data
        3. Using tokenization for card references
        4. Proper input validation to prevent injection attacks
        
        Returns True if compliant, False otherwise.
        """
        # Check if we're using a PCI-compliant payment processor/gateway
        if hasattr(transaction, 'payment_provider') and transaction.payment_provider:
            # List of known PCI-compliant payment providers
            pci_compliant_providers = [
                'stripe', 'paystack', 'flutterwave', 'paypal', 'square',
                'adyen', 'worldpay', 'checkout.com', 'authorize.net'
            ]
            
            if transaction.payment_provider.lower() not in pci_compliant_providers:
                return False
            
        # Check if card data is being handled securely (using tokenization)
        if hasattr(transaction, 'payment_method') and transaction.payment_method == 'card':
            metadata = {}
            
            # Get transaction metadata
            try:
                if hasattr(transaction, 'get_metadata'):
                    metadata = transaction.get_metadata()
                elif hasattr(transaction, 'metadata') and transaction.metadata:
                    if isinstance(transaction.metadata, str):
                        metadata = json.loads(transaction.metadata)
                    else:
                        metadata = transaction.metadata
            except (json.JSONDecodeError, AttributeError):
                metadata = {}
                
            # Check for raw card data in metadata (which would be a PCI violation)
            card_data_patterns = [
                r'\b(?:\d[ -]*?){13,16}\b',  # Credit card number pattern
                r'cvv\s*:\s*\d{3,4}',  # CVV pattern
                r'cvc\s*:\s*\d{3,4}'   # CVC pattern
            ]
            
            metadata_str = json.dumps(metadata).lower()
            
            for pattern in card_data_patterns:
                if re.search(pattern, metadata_str):
                    return False
                    
            # If using a token instead of raw card data, it's likely compliant
            if 'token' in metadata or 'card_token' in metadata:
                return True
                
        # Default to assuming compliant if no issues found
        return True

    @classmethod
    def perform_aml_check(cls, transaction) -> Tuple[bool, float, List[str], List[str]]:
        """
        Performs Anti-Money Laundering (AML) checks on the transaction.
        
        AML checks include:
        1. Transaction amount monitoring
        2. Transaction frequency analysis
        3. Cross-border transaction analysis
        4. Sanctions screening
        5. Politically Exposed Person (PEP) screening
        
        Returns a tuple of:
        - is_compliant (bool): Whether the transaction passes AML checks
        - risk_score (float): Risk score between 0.0 and 1.0
        - actions_required (list): List of required actions if any
        - reasons (list): List of reasons for the risk assessment
        """
        risk_score = 0.0
        reasons = []
        actions = []
        
        # 1. Check transaction amount (large transactions increase risk)
        amount_risk = cls._evaluate_transaction_amount(transaction)
        if amount_risk > 0:
            risk_score += amount_risk * 0.3  # 30% weight for amount
            if amount_risk > 0.7:
                reasons.append("Unusually large transaction amount")
                actions.append("enhanced_due_diligence")
        
        # 2. Check transaction patterns/frequency
        frequency_risk = cls._evaluate_transaction_frequency(transaction)
        if frequency_risk > 0:
            risk_score += frequency_risk * 0.2  # 20% weight for frequency
            if frequency_risk > 0.5:
                reasons.append("Unusual transaction pattern detected")
                
        # 3. Cross-border transaction check
        country_risk = cls._evaluate_country_risk(transaction)
        if country_risk > 0:
            risk_score += country_risk * 0.2  # 20% weight for country risk
            if country_risk > 0.7:
                reasons.append("High-risk jurisdiction involved in transaction")
                actions.append("country_risk_assessment")
                
        # 4. Sanctions screening
        is_sanctioned, sanctions_details = cls._check_sanctions_list(transaction)
        if is_sanctioned:
            risk_score += 1.0  # Maximum risk for sanctioned entities
            reasons.append(f"Match found on sanctions list: {sanctions_details}")
            actions.append("block_transaction")
            actions.append("file_report")
            
        # 5. PEP screening
        is_pep, pep_details = cls._check_pep_list(transaction)
        if is_pep:
            risk_score += 0.8  # High risk for PEPs but not automatic rejection
            reasons.append(f"Customer identified as politically exposed person: {pep_details}")
            actions.append("enhanced_due_diligence")
        
        # Cap risk score at 1.0
        risk_score = min(1.0, risk_score)
        
        # Determine compliance status based on risk score
        is_compliant = True
        if risk_score > cls.HIGH_RISK_THRESHOLD:
            is_compliant = False
            if "block_transaction" not in actions:
                actions.append("manual_review")
        elif risk_score > cls.MEDIUM_RISK_THRESHOLD:
            actions.append("enhanced_monitoring")
            
        return is_compliant, risk_score, actions, reasons

    @classmethod
    def check_kyc_requirements(cls, transaction) -> Tuple[bool, float, List[str], List[str]]:
        """
        Checks Know Your Customer (KYC) requirements for the transaction.
        
        KYC checks include:
        1. Verifying customer identity
        2. Address verification
        3. Document verification
        4. Risk-based approach for enhanced due diligence
        
        Returns a tuple of:
        - is_compliant (bool): Whether the transaction passes KYC checks
        - risk_score (float): Risk score between 0.0 and 1.0
        - actions_required (list): List of required actions if any
        - reasons (list): List of reasons for the risk assessment
        """
        risk_score = 0.0
        reasons = []
        actions = []
        
        # Get customer associated with transaction
        customer = None
        if hasattr(transaction, 'customer') and transaction.customer:
            customer = transaction.customer
            
        # If no customer object but we have customer information in transaction
        if not customer and hasattr(transaction, 'email'):
            # This would need to be adapted to your actual customer retrieval logic
            from .models import Customer
            try:
                customer = Customer.objects.filter(email=transaction.email).first()
            except:
                pass
            
        # No customer record found - automatic KYC fail for high-value transactions
        if not customer:
            # For low-value transactions, this might be acceptable risk
            if cls._is_high_value_transaction(transaction):
                risk_score += 0.9
                reasons.append("No customer record found for high-value transaction")
                actions.append("collect_customer_information")
                return False, risk_score, actions, reasons
            else:
                risk_score += 0.3
                reasons.append("No customer record found")
                actions.append("create_customer_record")
        else:
            # Check if customer has been KYC verified
            kyc_verified = getattr(customer, 'kyc_verified', False)
            kyc_level = getattr(customer, 'kyc_level', 0)
            
            if not kyc_verified:
                # Stronger requirements for high-value transactions
                if cls._is_high_value_transaction(transaction):
                    risk_score += 0.8
                    reasons.append("Customer not KYC verified for high-value transaction")
                    actions.append("complete_customer_verification")
                    return False, risk_score, actions, reasons
                else:
                    risk_score += 0.5
                    reasons.append("Customer not KYC verified")
                    actions.append("initiate_customer_verification")
            
            # Check if more verification is needed based on transaction value
            required_kyc_level = 1  # Basic KYC
            
            # Determine required KYC level based on transaction amount
            if cls._is_high_value_transaction(transaction):
                required_kyc_level = 3  # Enhanced KYC with document verification
            elif cls._is_medium_value_transaction(transaction):
                required_kyc_level = 2  # Standard KYC with address verification
                
            if kyc_level < required_kyc_level:
                risk_score += 0.3 * (required_kyc_level - kyc_level)
                reasons.append(f"Customer KYC level {kyc_level} insufficient for transaction (requires level {required_kyc_level})")
                actions.append("upgrade_kyc_level")
        
        # Determine compliance status based on risk score and actions
        is_compliant = "complete_customer_verification" not in actions
        
        return is_compliant, risk_score, actions, reasons

    @classmethod
    def validate_merchant_compliance(cls, merchant):
        """
        Validates if a merchant meets compliance requirements
        
        Returns:
        - is_compliant (bool): Whether the merchant is compliant
        - requirements (dict): Compliance requirements status
        - actions (list): Required actions to become compliant
        """
        requirements = {
            "pci_dss": False,
            "aml_program": False,
            "kyc_procedures": False,
            "data_protection": False,
            "terms_accepted": False
        }
        
        actions = []
        
        # Check if PCI-DSS self-assessment completed
        try:
            if hasattr(merchant, 'settings'):
                pci_complete = getattr(merchant.settings, 'pci_compliance_complete', False)
                requirements["pci_dss"] = pci_complete
                if not pci_complete:
                    actions.append("complete_pci_self_assessment")
        except:
            actions.append("complete_pci_self_assessment")
        
        # Check AML program acceptance
        try:
            if hasattr(merchant, 'settings'):
                aml_program = getattr(merchant.settings, 'aml_program_accepted', False)
                requirements["aml_program"] = aml_program
                if not aml_program:
                    actions.append("accept_aml_program")
        except:
            actions.append("accept_aml_program")
            
        # Check KYC procedures
        try:
            if hasattr(merchant, 'settings'):
                kyc_procedures = getattr(merchant.settings, 'kyc_procedures_accepted', False)
                requirements["kyc_procedures"] = kyc_procedures
                if not kyc_procedures:
                    actions.append("accept_kyc_procedures")
        except:
            actions.append("accept_kyc_procedures")
            
        # Check data protection agreement
        try:
            if hasattr(merchant, 'settings'):
                data_protection = getattr(merchant.settings, 'data_protection_accepted', False)
                requirements["data_protection"] = data_protection
                if not data_protection:
                    actions.append("accept_data_protection")
        except:
            actions.append("accept_data_protection")
            
        # Check terms of service acceptance
        try:
            if hasattr(merchant, 'settings'):
                terms_accepted = getattr(merchant.settings, 'terms_accepted', False)
                requirements["terms_accepted"] = terms_accepted
                if not terms_accepted:
                    actions.append("accept_terms_of_service")
        except:
            actions.append("accept_terms_of_service")
            
        # Check if merchant is in high-risk industry
        try:
            if hasattr(merchant, 'industry'):
                high_risk_industries = [
                    'gambling', 'adult', 'crypto', 'cbd', 'weapons', 
                    'dating', 'forex', 'binary options'
                ]
                if merchant.industry.lower() in high_risk_industries:
                    actions.append("complete_enhanced_due_diligence")
        except:
            pass
            
        # Is compliant if all requirements are met
        is_compliant = all(requirements.values())
        
        return is_compliant, requirements, actions

    # Helper methods
    
    @classmethod
    def _evaluate_transaction_amount(cls, transaction) -> float:
        """
        Evaluates risk based on transaction amount
        Returns a risk score between 0.0 and 1.0
        """
        try:
            # Normalize to USD for consistent comparison
            from .currency_service import CurrencyService
            
            amount = transaction.amount
            currency = transaction.currency if hasattr(transaction, 'currency') else 'USD'
            
            if currency != 'USD':
                try:
                    amount_usd = CurrencyService.convert_amount(amount, currency, 'USD')
                except:
                    # If conversion fails, use original amount
                    amount_usd = amount
            else:
                amount_usd = amount
                
            # Risk thresholds in USD
            low_threshold = 1000  # Transactions below this are low risk
            medium_threshold = 5000  # Transactions above low but below this are medium risk
            high_threshold = 10000  # Transactions above medium but below this are high risk
            very_high_threshold = 50000  # Transactions above this are very high risk
            
            if amount_usd < low_threshold:
                return 0.1  # Low risk
            elif amount_usd < medium_threshold:
                return 0.3  # Medium-low risk
            elif amount_usd < high_threshold:
                return 0.5  # Medium risk
            elif amount_usd < very_high_threshold:
                return 0.7  # High risk
            else:
                return 0.9  # Very high risk
                
        except Exception as e:
            logger.error(f"Error evaluating transaction amount: {e}")
            return 0.5  # Default to medium risk on error
    
    @classmethod
    def _evaluate_transaction_frequency(cls, transaction) -> float:
        """
        Evaluates risk based on transaction patterns and frequency
        Returns a risk score between 0.0 and 1.0
        """
        try:
            # This would typically require looking at transaction history
            # We'll implement a simplified version here
            
            # Check for customer and email
            customer = getattr(transaction, 'customer', None)
            email = getattr(transaction, 'email', None)
            
            if not customer and not email:
                return 0.5  # Medium risk if we can't evaluate history
                
            # Find recent transactions by this customer
            from .models import Transaction
            from django.utils import timezone
            
            # Lookback period of 24 hours
            time_threshold = timezone.now() - datetime.timedelta(hours=24)
            
            # Query recent transactions
            query = Q(created_at__gte=time_threshold)
            if customer:
                query &= Q(customer=customer)
            elif email:
                query &= Q(email=email)
                
            recent_transactions = Transaction.objects.filter(query).exclude(id=transaction.id)
            
            # Count transactions
            count = recent_transactions.count()
            
            # Evaluate frequency risk
            if count == 0:
                return 0.1  # Low risk - no recent transactions
            elif count <= 2:
                return 0.2  # Low-medium risk - few recent transactions
            elif count <= 5:
                return 0.4  # Medium risk - several recent transactions
            elif count <= 10:
                return 0.7  # High risk - many recent transactions
            else:
                return 0.9  # Very high risk - unusually high transaction volume
                
        except Exception as e:
            logger.error(f"Error evaluating transaction frequency: {e}")
            return 0.3  # Default to low-medium risk on error
    
    @classmethod
    def _evaluate_country_risk(cls, transaction) -> float:
        """
        Evaluates risk based on countries involved in the transaction
        Returns a risk score between 0.0 and 1.0
        """
        try:
            # Get country information from transaction or customer
            country_code = None
            
            # Try to get from transaction metadata
            metadata = {}
            try:
                if hasattr(transaction, 'get_metadata'):
                    metadata = transaction.get_metadata()
                elif hasattr(transaction, 'metadata') and transaction.metadata:
                    if isinstance(transaction.metadata, str):
                        metadata = json.loads(transaction.metadata)
                    else:
                        metadata = transaction.metadata
            except:
                metadata = {}
                
            # Look for country in metadata
            if metadata:
                country_code = metadata.get('country') or metadata.get('billing_country')
                
            # Try to get from customer
            if not country_code and hasattr(transaction, 'customer') and transaction.customer:
                country_code = getattr(transaction.customer, 'country', None)
                
            if not country_code:
                return 0.3  # Medium-low risk if country can't be determined
                
            # Normalize country code
            country_code = country_code.upper()
            
            # High-risk countries (FATF high-risk and non-cooperative jurisdictions)
            high_risk_countries = [
                'AF', 'KP', 'IR', 'MM', 'SY', 'YE', 'AL', 'BB',
                'BW', 'KH', 'HT', 'JM', 'MU', 'NI', 'PK', 'PA',
                'SY', 'ZW', 'UG', 'VU'
            ]
            
            # Medium-risk countries
            medium_risk_countries = [
                'RU', 'CN', 'BY', 'VE', 'IQ', 'LY', 'LB',
                'CU', 'SD', 'SS', 'BD', 'NG', 'PH', 'GH'
            ]
            
            if country_code in high_risk_countries:
                return 0.9  # Very high risk
            elif country_code in medium_risk_countries:
                return 0.6  # Medium-high risk
            else:
                return 0.1  # Low risk
                
        except Exception as e:
            logger.error(f"Error evaluating country risk: {e}")
            return 0.3  # Default to medium-low risk on error
    
    @classmethod
    def _check_sanctions_list(cls, transaction) -> Tuple[bool, str]:
        """
        Checks if the customer or related entities are on sanctions lists
        Returns a tuple of (is_sanctioned, details)
        """
        try:
            # In a real implementation, this would check against actual sanctions APIs
            # like OFAC, UN, EU sanctions lists
            
            # For demonstration, we'll simulate a simple check
            
            # Get customer name and details
            customer_name = ''
            customer_info = {}
            
            if hasattr(transaction, 'customer') and transaction.customer:
                customer = transaction.customer
                customer_name = f"{getattr(customer, 'first_name', '')} {getattr(customer, 'last_name', '')}"
                customer_info = {
                    'email': getattr(customer, 'email', ''),
                    'phone': getattr(customer, 'phone', ''),
                    'country': getattr(customer, 'country', '')
                }
            
            if not customer_name and hasattr(transaction, 'metadata'):
                try:
                    metadata = transaction.get_metadata() if hasattr(transaction, 'get_metadata') else {}
                    if metadata:
                        customer_name = metadata.get('customer_name', '')
                except:
                    pass
                    
            if not customer_name:
                return False, ""  # Can't check without a name
                
            # Get or create mock sanctions list
            sanctions_list = cache.get(cls.SANCTIONS_CACHE_KEY)
            if not sanctions_list:
                # In production, this would be populated from an API or database
                sanctions_list = [
                    {"name": "John Smith", "country": "IR", "reason": "OFAC SDN List"},
                    {"name": "Global Terror Org", "country": "SY", "reason": "OFAC Terrorism List"},
                    {"name": "Sanctioned Bank Ltd", "country": "KP", "reason": "EU Sanctions List"}
                ]
                cache.set(cls.SANCTIONS_CACHE_KEY, sanctions_list, cls.CACHE_TIMEOUT)
            
            # Check for name match (in production, use fuzzy matching)
            for entry in sanctions_list:
                if entry["name"].lower() in customer_name.lower():
                    return True, entry["reason"]
                    
            return False, ""
            
        except Exception as e:
            logger.error(f"Error checking sanctions list: {e}")
            return False, ""  # Default to not sanctioned on error
    
    @classmethod
    def _check_pep_list(cls, transaction) -> Tuple[bool, str]:
        """
        Checks if the customer is a Politically Exposed Person (PEP)
        Returns a tuple of (is_pep, details)
        """
        try:
            # In a real implementation, this would check against actual PEP databases
            
            # Get customer name and details
            customer_name = ''
            
            if hasattr(transaction, 'customer') and transaction.customer:
                customer = transaction.customer
                customer_name = f"{getattr(customer, 'first_name', '')} {getattr(customer, 'last_name', '')}"
            
            if not customer_name and hasattr(transaction, 'metadata'):
                try:
                    metadata = transaction.get_metadata() if hasattr(transaction, 'get_metadata') else {}
                    if metadata:
                        customer_name = metadata.get('customer_name', '')
                except:
                    pass
                    
            if not customer_name:
                return False, ""  # Can't check without a name
                
            # Get or create mock PEP list
            pep_list = cache.get(cls.PEP_CACHE_KEY)
            if not pep_list:
                # In production, this would be populated from an API or database
                pep_list = [
                    {"name": "James Wilson", "position": "Minister of Finance", "country": "UK"},
                    {"name": "Maria Garcia", "position": "Deputy Minister", "country": "ES"},
                    {"name": "Chen Wei", "position": "Provincial Governor", "country": "CN"}
                ]
                cache.set(cls.PEP_CACHE_KEY, pep_list, cls.CACHE_TIMEOUT)
            
            # Check for name match (in production, use fuzzy matching)
            for entry in pep_list:
                if entry["name"].lower() in customer_name.lower():
                    return True, f"{entry['position']}, {entry['country']}"
                    
            return False, ""
            
        except Exception as e:
            logger.error(f"Error checking PEP list: {e}")
            return False, ""  # Default to not PEP on error
    
    @classmethod
    def _is_high_value_transaction(cls, transaction) -> bool:
        """Determines if a transaction is considered high value"""
        try:
            from .currency_service import CurrencyService
            
            amount = transaction.amount
            currency = transaction.currency if hasattr(transaction, 'currency') else 'USD'
            
            if currency != 'USD':
                try:
                    amount_usd = CurrencyService.convert_amount(amount, currency, 'USD')
                except:
                    amount_usd = amount  # If conversion fails, use original amount
            else:
                amount_usd = amount
                
            return amount_usd >= 10000  # $10,000+ is high value
        except:
            # If any error occurs, be conservative
            return True
    
    @classmethod
    def _is_medium_value_transaction(cls, transaction) -> bool:
        """Determines if a transaction is considered medium value"""
        try:
            from .currency_service import CurrencyService
            
            amount = transaction.amount
            currency = transaction.currency if hasattr(transaction, 'currency') else 'USD'
            
            if currency != 'USD':
                try:
                    amount_usd = CurrencyService.convert_amount(amount, currency, 'USD')
                except:
                    amount_usd = amount  # If conversion fails, use original amount
            else:
                amount_usd = amount
                
            return 1000 <= amount_usd < 10000  # $1,000-$10,000 is medium value
        except:
            # If any error occurs, be conservative
            return True
    
    @classmethod
    def _log_compliance_check(cls, transaction, is_compliant, risk_score, details):
        """Logs a compliance check to the database and logging system"""
        # Log to standard logger
        if is_compliant:
            logger.info(f"Compliance check passed for transaction {transaction.reference} with risk score {risk_score}")
        else:
            logger.warning(f"Compliance check failed for transaction {transaction.reference} with risk score {risk_score}: {details}")
        
        # In production, also log to database
        try:
            # This would be implemented if you have a ComplianceLog model
            from .models import ComplianceLog
            ComplianceLog.objects.create(
                transaction=transaction,
                check_type='transaction',
                is_compliant=is_compliant,
                risk_score=risk_score,
                details=json.dumps(details)
            )
        except:
            # If model doesn't exist or other error, just continue
            pass


class PCI_DSS_Service:
    """Service for handling PCI-DSS compliance requirements"""
    
    # PCI DSS Compliance levels
    LEVEL_1 = 1  # >6M transactions annually - requires external audit
    LEVEL_2 = 2  # 1M-6M transactions annually - requires self-assessment
    LEVEL_3 = 3  # 20K-1M transactions annually - requires self-assessment
    LEVEL_4 = 4  # <20K transactions annually - requires self-assessment
    
    @classmethod
    def get_merchant_compliance_level(cls, merchant):
        """
        Determines the PCI DSS compliance level required for a merchant
        based on transaction volume
        """
        try:
            # Get annual transaction count
            from .models import Transaction
            from django.utils import timezone
            import datetime
            
            one_year_ago = timezone.now() - datetime.timedelta(days=365)
            transaction_count = Transaction.objects.filter(
                merchant=merchant,
                created_at__gte=one_year_ago
            ).count()
            
            if transaction_count > 6000000:
                return cls.LEVEL_1
            elif transaction_count > 1000000:
                return cls.LEVEL_2
            elif transaction_count > 20000:
                return cls.LEVEL_3
            else:
                return cls.LEVEL_4
        except:
            # Default to Level 4 (least stringent) if error
            return cls.LEVEL_4
    
    @classmethod
    def get_compliance_requirements(cls, level):
        """Gets the compliance requirements for a given PCI level"""
        requirements = {
            "annual_assessment": True,
            "quarterly_scan": False,
            "external_audit": False,
            "network_scan": False,
            "penetration_testing": False
        }
        
        if level <= cls.LEVEL_2:
            requirements["quarterly_scan"] = True
            
        if level == cls.LEVEL_1:
            requirements["external_audit"] = True
            requirements["network_scan"] = True
            requirements["penetration_testing"] = True
            
        return requirements
    
    @classmethod
    def tokenize_card_data(cls, card_data):
        """
        Tokenizes card data to meet PCI-DSS requirements
        In production, this would use a dedicated tokenization service
        """
        from .tokenization_service import TokenizationService
        
        return TokenizationService.tokenize_card(card_data)


class AML_Service:
    """Service for Anti-Money Laundering (AML) compliance"""
    
    @classmethod
    def generate_aml_report(cls, merchant, start_date=None, end_date=None):
        """Generates an AML compliance report for regulatory filing"""
        from django.utils import timezone
        import datetime
        
        if not start_date:
            start_date = timezone.now() - datetime.timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
            
        try:
            # Get transactions in the period
            from .models import Transaction
            
            transactions = Transaction.objects.filter(
                merchant=merchant,
                created_at__gte=start_date,
                created_at__lte=end_date
            )
            
            # Analyze for suspicious activity
            high_risk_transactions = []
            suspicious_patterns = []
            
            # Flag high risk transactions
            for transaction in transactions:
                # Use compliance service to evaluate
                is_compliant, risk_score, actions, reasons = ComplianceService.evaluate_transaction(transaction)
                
                if risk_score > ComplianceService.MEDIUM_RISK_THRESHOLD:
                    high_risk_transactions.append({
                        "id": transaction.id,
                        "reference": transaction.reference,
                        "amount": str(transaction.amount),
                        "currency": transaction.currency,
                        "date": transaction.created_at.isoformat(),
                        "risk_score": risk_score,
                        "reasons": reasons
                    })
            
            # Check for velocity patterns
            # (This would be more sophisticated in production)
            suspicious_patterns = cls._identify_suspicious_patterns(transactions)
            
            # Generate report
            report = {
                "merchant_id": merchant.id,
                "merchant_name": merchant.name,
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "total_transactions": transactions.count(),
                "total_volume": sum(t.amount for t in transactions),
                "high_risk_transactions": high_risk_transactions,
                "suspicious_patterns": suspicious_patterns,
                "generated_at": timezone.now().isoformat()
            }
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating AML report: {e}")
            return {"error": str(e)}
            
    @classmethod
    def _identify_suspicious_patterns(cls, transactions):
        """Identifies suspicious patterns in transaction data"""
        patterns = []
        
        # Group transactions by email/customer
        transaction_map = {}
        
        for transaction in transactions:
            key = transaction.email
            if key not in transaction_map:
                transaction_map[key] = []
            transaction_map[key].append(transaction)
            
        # Check for structuring (multiple smaller transactions)
        for email, txns in transaction_map.items():
            if len(txns) >= 3:
                # Check if multiple smaller transactions in short time
                txns.sort(key=lambda t: t.created_at)
                
                if (txns[-1].created_at - txns[0].created_at).total_seconds() <= 86400:  # 24 hours
                    total_value = sum(t.amount for t in txns)
                    
                    # If total value is significant and no single transaction is large
                    if total_value > 10000 and all(t.amount < 5000 for t in txns):
                        patterns.append({
                            "type": "possible_structuring",
                            "email": email,
                            "transaction_count": len(txns),
                            "total_value": str(total_value),
                            "time_span_hours": (txns[-1].created_at - txns[0].created_at).total_seconds() / 3600
                        })
        
        return patterns


class KYC_Service:
    """Service for Know Your Customer (KYC) compliance"""
    
    # KYC verification levels
    LEVEL_BASIC = 1    # Email verification
    LEVEL_STANDARD = 2 # ID verification
    LEVEL_ENHANCED = 3 # ID + Address + Document verification
    
    @classmethod
    def verify_customer(cls, customer, verification_level=LEVEL_BASIC, verification_data=None):
        """
        Verifies a customer's identity according to KYC requirements
        
        Args:
            customer: Customer object
            verification_level: Level of verification to perform
            verification_data: Dict containing verification documents/data
            
        Returns:
            dict: Verification result with status and details
        """
        result = {
            "success": False,
            "verification_level": verification_level,
            "details": {}
        }
        
        # Basic verification - email only
        if verification_level >= cls.LEVEL_BASIC:
            email_verified = cls._verify_email(customer)
            result["details"]["email_verified"] = email_verified
            if not email_verified:
                return result
        
        # Standard verification - ID check
        if verification_level >= cls.LEVEL_STANDARD:
            if not verification_data or "id_document" not in verification_data:
                result["details"]["id_verified"] = False
                result["details"]["error"] = "ID document required for standard verification"
                return result
                
            id_verified, id_details = cls._verify_identity_document(
                verification_data["id_document"],
                customer.first_name,
                customer.last_name
            )
            result["details"]["id_verified"] = id_verified
            result["details"].update(id_details)
            
            if not id_verified:
                return result
        
        # Enhanced verification - Address + additional documents
        if verification_level >= cls.LEVEL_ENHANCED:
            if not verification_data or "address_document" not in verification_data:
                result["details"]["address_verified"] = False
                result["details"]["error"] = "Address document required for enhanced verification"
                return result
                
            address_verified, address_details = cls._verify_address(
                verification_data["address_document"],
                customer
            )
            result["details"]["address_verified"] = address_verified
            result["details"].update(address_details)
            
            if not address_verified:
                return result
        
        # If we've reached this point, verification passed for the requested level
        result["success"] = True
        
        # Update customer record
        try:
            customer.kyc_verified = True
            customer.kyc_level = verification_level
            customer.verification_date = datetime.datetime.now()
            customer.save(update_fields=['kyc_verified', 'kyc_level', 'verification_date'])
        except Exception as e:
            logger.error(f"Error updating customer record after KYC: {e}")
            # Still return success since verification passed
        
        return result
    
    @classmethod
    def _verify_email(cls, customer):
        """Verifies customer email address"""
        # In production, this would send a verification email with token
        # For this demo, we'll assume email is verified if it exists
        return bool(customer.email)
    
    @classmethod
    def _verify_identity_document(cls, document_data, first_name, last_name):
        """
        Verifies an identity document (passport, ID card, etc.)
        In production, this would integrate with ID verification APIs
        """
        # Mock implementation
        try:
            # Simulate document checks
            details = {
                "document_type": document_data.get("type", "unknown"),
                "document_number": document_data.get("number", ""),
                "document_country": document_data.get("country", ""),
                "verification_method": "mock",
                "verification_timestamp": datetime.datetime.now().isoformat()
            }
            
            # In production, this would do actual verification
            # For demo, verify if document has required fields
            required_fields = ["type", "number", "country", "expiry_date", "image"]
            is_valid = all(field in document_data for field in required_fields)
            
            # Check document expiration
            if is_valid and "expiry_date" in document_data:
                try:
                    expiry = datetime.datetime.fromisoformat(document_data["expiry_date"])
                    if expiry <= datetime.datetime.now():
                        details["error"] = "Document expired"
                        return False, details
                except:
                    details["error"] = "Invalid expiry date format"
                    return False, details
            
            return is_valid, details
            
        except Exception as e:
            logger.error(f"Error verifying identity document: {e}")
            return False, {"error": str(e)}
    
    @classmethod
    def _verify_address(cls, address_document, customer):
        """
        Verifies customer address against provided document
        In production, this would integrate with address verification APIs
        """
        # Mock implementation
        try:
            details = {
                "document_type": address_document.get("type", "unknown"),
                "verification_method": "mock",
                "verification_timestamp": datetime.datetime.now().isoformat()
            }
            
            # In production, this would do actual address verification
            # For demo, verify if document has required fields and matches customer address
            required_fields = ["type", "image", "address_line", "city", "country"]
            is_valid = all(field in address_document for field in required_fields)
            
            # Check if address matches customer record
            if is_valid:
                customer_address = getattr(customer, "address", "")
                document_address = address_document.get("address_line", "")
                
                # Basic string matching (in production, use address parsing/normalization)
                address_match = customer_address.lower() in document_address.lower() or document_address.lower() in customer_address.lower()
                
                if not address_match:
                    details["error"] = "Address on document doesn't match customer record"
                    return False, details
            
            return is_valid, details
            
        except Exception as e:
            logger.error(f"Error verifying address document: {e}")
            return False, {"error": str(e)}


# Initialize and export the services
compliance_service = ComplianceService()
pci_service = PCI_DSS_Service()
aml_service = AML_Service()
kyc_service = KYC_Service()