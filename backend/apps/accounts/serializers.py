import secrets

from django.core.mail import send_mail
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
            'id', 'email', 'phone_number', 'role', 'parent', 'commission_rate',
            'first_name', 'last_name',
            'min_bet_amount', 'max_bet_amount', 'is_betting_enabled', 'date_joined'
        )
        read_only_fields = (
            'id', 'date_joined', 'role', 'parent', 'commission_rate'
        )


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password]
    )
    password2 = serializers.CharField(write_only=True, required=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, default='')

    class Meta:
        model = User
        fields = ('email', 'password', 'password2', 'first_name', 'last_name', 'phone_number')

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
            'id', 'email', 'phone_number', 'role', 'parent', 'commission_rate',
            'first_name', 'last_name',
            'min_bet_amount', 'max_bet_amount', 'is_betting_enabled'
        )
        read_only_fields = ('id', 'email')

    def validate(self, attrs):
        from .permissions import roles_assignable_by, user_can_manage_target

        request = self.context.get('request')
        actor = getattr(request, 'user', None)
        target = self.instance

        if actor is None or target is None:
            return attrs

        if not user_can_manage_target(actor, target):
            raise serializers.ValidationError('You cannot manage this user.')

        allowed_roles = roles_assignable_by(actor)
        new_role = attrs.get('role', target.role)
        if new_role not in allowed_roles:
            raise serializers.ValidationError(
                {'role': 'You are not allowed to assign that role.'}
            )

        if 'parent' in attrs and attrs['parent'] != target.parent:
            is_full_admin = actor.is_superuser or actor.is_staff or actor.role == User.Role.ADMIN
            if not is_full_admin:
                raise serializers.ValidationError(
                    {'parent': 'You are not allowed to reassign this user\'s parent.'}
                )

        return attrs


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

            # Actually send an email with the token
            send_mail(
                subject='QuickPayBet Password Reset',
                message=(
                    f'Hello {user.email},\n\n'
                    f'You requested a password reset.\n'
                    f'Your reset token is:\n\n{token}\n\n'
                    'Use it with the Password Reset Confirm endpoint:\n'
                    'POST /api/auth/password-reset/confirm/\n\n'
                    'If you did not request this, please ignore this email.'
                ),
                from_email=None,
                recipient_list=[user.email],
                fail_silently=False,
            )
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
