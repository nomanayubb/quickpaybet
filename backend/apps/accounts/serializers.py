import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import PasswordResetToken

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id', 'email', 'role', 'parent', 'first_name', 'last_name',
            'min_bet_amount', 'max_bet_amount', 'is_betting_enabled', 'date_joined'
        )
        read_only_fields = ('id', 'date_joined', 'role', 'parent')


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password]
    )
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ('email', 'password', 'password2', 'first_name', 'last_name')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError(
                {'password': 'Passwords must match.'}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        return User.objects.create_user(**validated_data)


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id', 'email', 'role', 'parent', 'first_name', 'last_name',
            'min_bet_amount', 'max_bet_amount', 'is_betting_enabled'
        )
        read_only_fields = ('id', 'email')


class RequestPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def save(self):
        email = self.validated_data['email']
        try:
            user = User.objects.get(email=email)
            # Invalidate any prior outstanding reset tokens for this user.
            PasswordResetToken.objects.filter(
                user=user,
                is_used=False,
            ).update(is_used=True)

            # Create a fresh token.
            token = secrets.token_urlsafe(40)
            PasswordResetToken.objects.create(
                user=user,
                token=token,
            )
            # In a real implementation you would send the token by email.
            # For development, the token is returned only in DEBUG mode.
        except User.DoesNotExist:
            pass
        return {'email': email}


class ConfirmPasswordResetSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True,
        validators=[validate_password]
    )
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'new_password': 'Passwords must match.'}
            )

        try:
            reset_obj = PasswordResetToken.objects.get(
                token=attrs['token'],
                is_used=False,
            )
        except PasswordResetToken.DoesNotExist:
            raise serializers.ValidationError(
                {'token': 'Invalid or expired reset token.'}
            )

        attrs['reset_obj'] = reset_obj
        return attrs

    def save(self):
        reset_obj = self.validated_data['reset_obj']
        user = reset_obj.user
        user.set_password(self.validated_data['new_password'])
        user.save()
        reset_obj.is_used = True
        reset_obj.save()
