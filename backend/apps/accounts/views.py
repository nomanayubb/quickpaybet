from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .permissions import IsAdminOrMaster
from .serializers import (
    RegisterSerializer,
    UserSerializer,
    AdminUserUpdateSerializer,
    RequestPasswordResetSerializer,
    ConfirmPasswordResetSerializer,
)

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)

        data = UserSerializer(user).data
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)

        return Response(data, status=status.HTTP_201_CREATED)


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class AdminUserUpdateView(generics.RetrieveUpdateAPIView):
    serializer_class = AdminUserUpdateSerializer
    queryset = User.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsAdminOrMaster]


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RequestPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'detail': 'Password reset instructions sent if the email exists.'},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = ConfirmPasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'detail': 'Password has been reset.'},
            status=status.HTTP_200_OK,
        )
