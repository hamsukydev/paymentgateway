from rest_framework import serializers
from ..models import Transaction, Customer

class TransactionSerializer(serializers.ModelSerializer):
    # Custom field for metadata that will handle JSON serialization/deserialization
    metadata = serializers.JSONField(required=False, allow_null=True)
    
    class Meta:
        model = Transaction
        fields = ['id', 'reference', 'amount', 'currency', 'customer', 'email', 
                  'status', 'description', 'metadata', 'payment_method', 
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'reference', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        # If customer email exists but no customer instance is provided, find or create the customer
        email = validated_data.get('email')
        if email and not validated_data.get('customer'):
            customer, created = Customer.objects.get_or_create(
                email=email,
                defaults={'name': email.split('@')[0]}
            )
            validated_data['customer'] = customer
            
        # Generate a unique reference if not provided
        if 'reference' not in validated_data:
            validated_data['reference'] = Transaction.generate_reference()
        
        # Extract metadata before creating the transaction
        metadata = validated_data.pop('metadata', None)
        
        # Create transaction
        transaction = Transaction.objects.create(**validated_data)
        
        # Set metadata if it exists
        if metadata is not None:
            transaction.set_metadata(metadata)
            transaction.save()
            
        return transaction
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Get metadata from the instance's get_metadata method
        representation['metadata'] = instance.get_metadata()
        return representation
        
class InitiateTransactionSerializer(serializers.Serializer):
    email = serializers.EmailField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.ChoiceField(choices=['NGN', 'USD', 'EUR', 'GBP'], default='NGN')
    description = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)
    callback_url = serializers.URLField(required=False)
    
class VerifyTransactionSerializer(serializers.Serializer):
    reference = serializers.CharField()