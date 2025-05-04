"""
Analytics Service for Payment Gateway

This module handles API usage tracking, performance monitoring, and analytics for the payment gateway.
It provides functionality to track API calls, measure performance metrics, and generate reports.
"""

import json
import logging
import statistics
from datetime import datetime, timedelta
from collections import defaultdict, Counter

from django.conf import settings
from django.db import models, connection
from django.db.models import Count, Sum, Avg, F, ExpressionWrapper, fields
from django.utils import timezone
from django.core.cache import cache

from .models import Transaction, Merchant, APIRequest

logger = logging.getLogger(__name__)

class AnalyticsService:
    """
    Service for tracking API usage, performance and generating analytics
    """
    
    @staticmethod
    def track_api_request(merchant_id, endpoint, method, status_code, 
                         response_time, payload=None, error=None):
        """
        Track an API request for analytics and rate limiting
        
        Args:
            merchant_id: ID of the merchant making the request
            endpoint: API endpoint accessed
            method: HTTP method (GET, POST, etc.)
            status_code: HTTP status code of the response
            response_time: Request processing time in milliseconds
            payload: Request payload (optional)
            error: Error message if request failed (optional)
        """
        try:
            # Create API request record
            APIRequest.objects.create(
                merchant_id=merchant_id,
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                response_time=response_time,
                is_error=(status_code >= 400),
                error_message=error if error else None,
                request_payload=json.dumps(payload) if payload else None
            )
            
            # Update real-time metrics in cache
            AnalyticsService._update_realtime_metrics(merchant_id, endpoint, status_code, response_time)
            
        except Exception as e:
            logger.error(f"Failed to track API request: {str(e)}")
    
    @staticmethod
    def _update_realtime_metrics(merchant_id, endpoint, status_code, response_time):
        """
        Update real-time metrics in cache for quick access
        
        Args:
            merchant_id: ID of the merchant
            endpoint: API endpoint
            status_code: HTTP status code
            response_time: Response time in ms
        """
        now = timezone.now()
        current_minute = now.replace(second=0, microsecond=0)
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        
        # Keys for different time windows
        minute_key = f"api_metrics:minute:{merchant_id}:{current_minute.timestamp()}"
        hour_key = f"api_metrics:hour:{merchant_id}:{current_hour.timestamp()}"
        endpoint_key = f"api_metrics:endpoint:{merchant_id}:{endpoint}:{current_hour.timestamp()}"
        
        # Transaction pipeline
        with cache.lock(f"analytics_lock:{merchant_id}", timeout=5):
            # Minute-level metrics
            minute_data = cache.get(minute_key) or {
                'count': 0, 'errors': 0, 'response_times': []
            }
            minute_data['count'] += 1
            if status_code >= 400:
                minute_data['errors'] += 1
            minute_data['response_times'].append(response_time)
            cache.set(minute_key, minute_data, 3600)  # Keep for an hour
            
            # Hour-level metrics
            hour_data = cache.get(hour_key) or {
                'count': 0, 'errors': 0, 'endpoints': Counter(), 
                'status_codes': Counter(), 'avg_response_time': 0
            }
            hour_data['count'] += 1
            if status_code >= 400:
                hour_data['errors'] += 1
            hour_data['endpoints'][endpoint] += 1
            hour_data['status_codes'][str(status_code)] += 1
            
            # Update average response time using cumulative moving average
            hour_data['avg_response_time'] = (
                (hour_data['avg_response_time'] * (hour_data['count'] - 1) + response_time) / 
                hour_data['count']
            )
            cache.set(hour_key, hour_data, 86400)  # Keep for a day
            
            # Endpoint-specific metrics
            endpoint_data = cache.get(endpoint_key) or {
                'count': 0, 'errors': 0, 'response_times': [],
                'status_codes': Counter()
            }
            endpoint_data['count'] += 1
            if status_code >= 400:
                endpoint_data['errors'] += 1
            endpoint_data['status_codes'][str(status_code)] += 1
            
            # Keep last 100 response times for percentile calculations
            endpoint_data['response_times'].append(response_time)
            if len(endpoint_data['response_times']) > 100:
                endpoint_data['response_times'].pop(0)
                
            cache.set(endpoint_key, endpoint_data, 86400)  # Keep for a day
    
    @staticmethod
    def get_merchant_metrics(merchant_id, period='day'):
        """
        Get API usage and performance metrics for a merchant
        
        Args:
            merchant_id: ID of the merchant
            period: Time period for metrics ('hour', 'day', 'week', 'month')
            
        Returns:
            dict: Merchant API metrics
        """
        now = timezone.now()
        
        # Determine time range based on period
        if period == 'hour':
            start_time = now - timedelta(hours=1)
        elif period == 'day':
            start_time = now - timedelta(days=1)
        elif period == 'week':
            start_time = now - timedelta(weeks=1)
        elif period == 'month':
            start_time = now - timedelta(days=30)
        else:
            start_time = now - timedelta(days=1)  # Default to 1 day
        
        # Get API requests in the time period
        api_requests = APIRequest.objects.filter(
            merchant_id=merchant_id,
            timestamp__gte=start_time
        )
        
        # Basic metrics
        total_requests = api_requests.count()
        if total_requests == 0:
            return {
                'period': period,
                'total_requests': 0,
                'error_rate': 0,
                'avg_response_time': 0,
                'top_endpoints': [],
                'status_code_distribution': {},
                'requests_over_time': []
            }
        
        error_requests = api_requests.filter(is_error=True).count()
        error_rate = (error_requests / total_requests) * 100 if total_requests > 0 else 0
        
        # Response time metrics
        avg_response_time = api_requests.aggregate(avg=Avg('response_time'))['avg'] or 0
        
        # Top endpoints
        top_endpoints = api_requests.values('endpoint').annotate(
            count=Count('id'),
            avg_time=Avg('response_time'),
            error_count=Count('id', filter=models.Q(is_error=True))
        ).order_by('-count')[:10]
        
        # Status code distribution
        status_codes = api_requests.values('status_code').annotate(
            count=Count('id')
        ).order_by('status_code')
        
        status_code_distribution = {
            str(item['status_code']): item['count'] 
            for item in status_codes
        }
        
        # Requests over time (with time bucket based on period)
        if period in ['hour']:
            # Group by minute for hourly view
            trunc_unit = 'minute'
        elif period in ['day']:
            # Group by hour for daily view
            trunc_unit = 'hour'
        else:
            # Group by day for weekly/monthly view
            trunc_unit = 'day'
        
        # Use Django's built-in time truncation
        requests_over_time = AnalyticsService._get_requests_over_time(
            api_requests, trunc_unit, start_time, now
        )
        
        # Combine everything into metrics dictionary
        return {
            'period': period,
            'total_requests': total_requests,
            'error_rate': round(error_rate, 2),
            'avg_response_time': round(avg_response_time, 2),
            'top_endpoints': list(top_endpoints),
            'status_code_distribution': status_code_distribution,
            'requests_over_time': requests_over_time
        }
    
    @staticmethod
    def _get_requests_over_time(api_requests, trunc_unit, start_time, end_time):
        """
        Get request counts over time with appropriate time bucketing
        
        Args:
            api_requests: QuerySet of API requests
            trunc_unit: Time unit to group by ('minute', 'hour', 'day')
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            list: Time series data with request counts
        """
        # SQL query using Django ORM to get time-bucketed data
        query = f"""
        SELECT 
            DATE_TRUNC('{trunc_unit}', "timestamp") as time_bucket,
            COUNT(*) as count,
            SUM(CASE WHEN is_error = TRUE THEN 1 ELSE 0 END) as errors,
            AVG(response_time) as avg_time
        FROM 
            payments_apirequest
        WHERE 
            merchant_id = %s AND
            timestamp BETWEEN %s AND %s
        GROUP BY 
            time_bucket
        ORDER BY 
            time_bucket ASC
        """
        
        with connection.cursor() as cursor:
            cursor.execute(
                query, 
                [api_requests.first().merchant_id, start_time, end_time]
            )
            results = cursor.fetchall()
        
        # Format results
        time_series = [
            {
                'timestamp': time_bucket.isoformat(),
                'count': count,
                'errors': errors,
                'avg_response_time': round(float(avg_time), 2) if avg_time else 0
            }
            for time_bucket, count, errors, avg_time in results
        ]
        
        return time_series
    
    @staticmethod
    def get_system_performance_metrics():
        """
        Get overall system performance metrics
        
        Returns:
            dict: System performance metrics
        """
        now = timezone.now()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        
        # Get recent API requests
        recent_requests = APIRequest.objects.filter(
            timestamp__gte=hour_ago
        )
        
        daily_requests = APIRequest.objects.filter(
            timestamp__gte=day_ago
        )
        
        # Calculate metrics
        hourly_request_count = recent_requests.count()
        daily_request_count = daily_requests.count()
        error_rate = (
            recent_requests.filter(is_error=True).count() / hourly_request_count
            if hourly_request_count > 0 else 0
        ) * 100
        
        # Response time metrics
        avg_response_time = recent_requests.aggregate(avg=Avg('response_time'))['avg'] or 0
        
        # Endpoint performance
        endpoint_performance = recent_requests.values('endpoint').annotate(
            count=Count('id'),
            avg_time=Avg('response_time'),
            error_count=Count('id', filter=models.Q(is_error=True))
        ).order_by('-count')[:10]
        
        # Build metrics dictionary
        return {
            'timestamp': now.isoformat(),
            'hourly_request_count': hourly_request_count,
            'daily_request_count': daily_request_count,
            'error_rate': round(error_rate, 2),
            'avg_response_time': round(avg_response_time, 2),
            'endpoint_performance': list(endpoint_performance),
        }
    
    @staticmethod
    def get_transaction_metrics(merchant_id, period='day'):
        """
        Get transaction metrics for a merchant
        
        Args:
            merchant_id: ID of the merchant
            period: Time period for metrics ('day', 'week', 'month', 'year')
            
        Returns:
            dict: Transaction metrics
        """
        now = timezone.now()
        
        # Determine time range based on period
        if period == 'day':
            start_time = now - timedelta(days=1)
        elif period == 'week':
            start_time = now - timedelta(weeks=1)
        elif period == 'month':
            start_time = now - timedelta(days=30)
        elif period == 'year':
            start_time = now - timedelta(days=365)
        else:
            start_time = now - timedelta(days=30)  # Default to 1 month
        
        # Get transactions in the time period
        transactions = Transaction.objects.filter(
            merchant_id=merchant_id,
            created_at__gte=start_time
        )
        
        # Basic metrics
        total_txns = transactions.count()
        if total_txns == 0:
            return {
                'period': period,
                'total_transactions': 0,
                'total_volume': 0,
                'successful_transactions': 0,
                'success_rate': 0,
                'avg_transaction_value': 0,
                'currency_breakdown': {},
                'transactions_over_time': []
            }
        
        successful_txns = transactions.filter(status='success').count()
        success_rate = (successful_txns / total_txns) * 100 if total_txns > 0 else 0
        
        # Transaction volume metrics
        total_volume = transactions.filter(status='success').aggregate(
            Sum('amount')
        )['amount__sum'] or 0
        
        avg_transaction_value = (
            total_volume / successful_txns if successful_txns > 0 else 0
        )
        
        # Currency breakdown
        currency_breakdown = transactions.filter(status='success').values(
            'currency'
        ).annotate(
            count=Count('id'),
            volume=Sum('amount')
        )
        
        # Format currency breakdown
        currency_data = {
            item['currency']: {
                'count': item['count'],
                'volume': float(item['volume'])
            }
            for item in currency_breakdown
        }
        
        # Get transactions over time
        if period in ['day']:
            # Group by hour for daily view
            trunc_unit = 'hour'
        elif period in ['week']:
            # Group by day for weekly view
            trunc_unit = 'day'
        else:
            # Group by day for monthly/yearly view
            trunc_unit = 'day'
            
        transactions_over_time = AnalyticsService._get_transactions_over_time(
            transactions, trunc_unit, start_time, now
        )
        
        # Combine everything into metrics dictionary
        return {
            'period': period,
            'total_transactions': total_txns,
            'total_volume': float(total_volume),
            'successful_transactions': successful_txns,
            'success_rate': round(success_rate, 2),
            'avg_transaction_value': float(avg_transaction_value),
            'currency_breakdown': currency_data,
            'transactions_over_time': transactions_over_time
        }
    
    @staticmethod
    def _get_transactions_over_time(transactions, trunc_unit, start_time, end_time):
        """
        Get transaction counts and volumes over time with appropriate time bucketing
        
        Args:
            transactions: QuerySet of transactions
            trunc_unit: Time unit to group by ('hour', 'day')
            start_time: Start of time range
            end_time: End of time range
            
        Returns:
            list: Time series data with transaction counts and volumes
        """
        # SQL query using Django ORM to get time-bucketed data
        query = f"""
        SELECT 
            DATE_TRUNC('{trunc_unit}', created_at) as time_bucket,
            COUNT(*) as count,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status = 'success' THEN amount ELSE 0 END) as volume
        FROM 
            payments_transaction
        WHERE 
            merchant_id = %s AND
            created_at BETWEEN %s AND %s
        GROUP BY 
            time_bucket
        ORDER BY 
            time_bucket ASC
        """
        
        with connection.cursor() as cursor:
            cursor.execute(
                query, 
                [transactions.first().merchant_id, start_time, end_time]
            )
            results = cursor.fetchall()
        
        # Format results
        time_series = [
            {
                'timestamp': time_bucket.isoformat(),
                'count': count,
                'successful': successful,
                'volume': float(volume) if volume else 0
            }
            for time_bucket, count, successful, volume in results
        ]
        
        return time_series
        
    @staticmethod
    def generate_merchant_dashboard_data(merchant_id):
        """
        Generate comprehensive metrics for merchant dashboard
        
        Args:
            merchant_id: ID of the merchant
            
        Returns:
            dict: Dashboard data with various metrics
        """
        # Get merchant object
        try:
            merchant = Merchant.objects.get(id=merchant_id)
        except Merchant.DoesNotExist:
            logger.error(f"Merchant with ID {merchant_id} not found")
            return {"error": "Merchant not found"}
        
        # Time ranges
        now = timezone.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        this_month = today.replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)
        
        # Get transactions for different time periods
        today_txns = Transaction.objects.filter(
            merchant=merchant,
            created_at__gte=today
        )
        yesterday_txns = Transaction.objects.filter(
            merchant=merchant,
            created_at__gte=yesterday,
            created_at__lt=today
        )
        this_month_txns = Transaction.objects.filter(
            merchant=merchant,
            created_at__gte=this_month
        )
        last_month_txns = Transaction.objects.filter(
            merchant=merchant,
            created_at__gte=last_month,
            created_at__lt=this_month
        )
        
        # Calculate key metrics
        today_volume = today_txns.filter(status='success').aggregate(
            Sum('amount')
        )['amount__sum'] or 0
        
        yesterday_volume = yesterday_txns.filter(status='success').aggregate(
            Sum('amount')
        )['amount__sum'] or 0
        
        this_month_volume = this_month_txns.filter(status='success').aggregate(
            Sum('amount')
        )['amount__sum'] or 0
        
        last_month_volume = last_month_txns.filter(status='success').aggregate(
            Sum('amount')
        )['amount__sum'] or 0
        
        # Calculate percentage changes
        daily_change_pct = (
            ((today_volume - yesterday_volume) / yesterday_volume) * 100
            if yesterday_volume > 0 else 0
        )
        
        monthly_change_pct = (
            ((this_month_volume - last_month_volume) / last_month_volume) * 100
            if last_month_volume > 0 else 0
        )
        
        # Transaction success rate
        today_success_rate = (
            (today_txns.filter(status='success').count() / today_txns.count()) * 100
            if today_txns.count() > 0 else 0
        )
        
        this_month_success_rate = (
            (this_month_txns.filter(status='success').count() / this_month_txns.count()) * 100
            if this_month_txns.count() > 0 else 0
        )
        
        # Get recent transactions
        recent_transactions = Transaction.objects.filter(
            merchant=merchant
        ).order_by('-created_at')[:10].values(
            'id', 'reference', 'amount', 'currency', 'status', 
            'payment_method', 'created_at', 'customer_email'
        )
        
        # Combine into dashboard data
        dashboard_data = {
            'merchant': {
                'id': merchant.id,
                'name': merchant.name,
                'email': merchant.email,
                'created_at': merchant.created_at.isoformat()
            },
            'summary': {
                'today_volume': float(today_volume),
                'today_count': today_txns.filter(status='success').count(),
                'today_success_rate': round(today_success_rate, 2),
                'daily_change_pct': round(daily_change_pct, 2),
                
                'this_month_volume': float(this_month_volume),
                'this_month_count': this_month_txns.filter(status='success').count(),
                'this_month_success_rate': round(this_month_success_rate, 2),
                'monthly_change_pct': round(monthly_change_pct, 2),
            },
            'recent_transactions': list(recent_transactions),
        }
        
        # Add additional metrics
        dashboard_data.update({
            'transaction_metrics': AnalyticsService.get_transaction_metrics(merchant_id, 'month'),
            'api_metrics': AnalyticsService.get_merchant_metrics(merchant_id, 'day')
        })
        
        return dashboard_data