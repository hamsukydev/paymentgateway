from rest_framework import serializers
from ..models import PaymentPlan

class PaymentPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentPlan
        fields = ['id', 'name', 'amount', 'currency', 'interval', 
                 'description', 'active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']