from rest_framework import serializers
from django.contrib.auth.models import User
from ..models import Merchant

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'email']
        read_only_fields = ['id']

class MerchantSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    
    class Meta:
        model = Merchant
        fields = [
            'id', 'user', 'business_name', 'business_email', 'business_phone',
            'business_address', 'business_description', 'website', 'industry',
            'verification_status', 'public_key', 'is_active', 
            'transaction_fee_percentage', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'public_key', 'secret_key', 'created_at', 'updated_at']

class MerchantRegistrationSerializer(serializers.Serializer):
    # User fields
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    
    # Merchant fields
    business_name = serializers.CharField(max_length=255)
    business_email = serializers.EmailField()
    business_phone = serializers.CharField(max_length=20)
    business_address = serializers.CharField()
    business_description = serializers.CharField(required=False, allow_blank=True)
    website = serializers.URLField(required=False, allow_blank=True, allow_null=True)
    industry = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    
    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already in use.")
        return value
        
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email is already in use.")
        return value
        
    def validate_business_email(self, value):
        if Merchant.objects.filter(business_email=value).exists():
            raise serializers.ValidationError("This business email is already in use.")
        return value
    
    def create(self, validated_data):
        # Create User
        user_data = {
            'username': validated_data['username'],
            'email': validated_data['email'],
            'password': validated_data['password'],
            'first_name': validated_data.get('first_name', ''),
            'last_name': validated_data.get('last_name', '')
        }
        
        user = User.objects.create_user(**{k: v for k, v in user_data.items() if k != 'password'})
        user.set_password(user_data['password'])
        user.save()
        
        # Create Merchant
        merchant_data = {
            'user': user,
            'business_name': validated_data['business_name'],
            'business_email': validated_data['business_email'],
            'business_phone': validated_data['business_phone'],
            'business_address': validated_data['business_address'],
            'business_description': validated_data.get('business_description', ''),
            'website': validated_data.get('website'),
            'industry': validated_data.get('industry'),
            'public_key': Merchant.generate_public_key(),
            'secret_key': Merchant.generate_secret_key(),
        }
        
        merchant = Merchant.objects.create(**merchant_data)
        return merchant