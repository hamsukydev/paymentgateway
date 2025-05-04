from rest_framework import serializers
from ..models import Subscription, Customer, PaymentPlan

class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ['id', 'customer', 'plan', 'reference', 'status', 
                 'next_payment_date', 'created_at', 'updated_at']
        read_only_fields = ['id', 'reference', 'created_at', 'updated_at']
        
    def create(self, validated_data):
        # Generate a unique reference if not provided
        if 'reference' not in validated_data:
            import uuid
            validated_data['reference'] = f"SUB-{uuid.uuid4().hex[:8].upper()}"
        
        return super().create(validated_data)
        
class CreateSubscriptionSerializer(serializers.Serializer):
    email = serializers.EmailField()
    plan_id = serializers.IntegerField()
    start_date = serializers.DateTimeField(required=False)
    metadata = serializers.JSONField(required=False)