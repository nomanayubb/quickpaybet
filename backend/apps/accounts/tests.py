from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

User = get_user_model()


class UserManagerTests(TestCase):
    def test_create_user(self):
        user = User.objects.create_user(
            email='user@example.com',
            password='testpass123',
            role=User.Role.USER,
        )
        self.assertEqual(user.email, 'user@example.com')
        self.assertTrue(user.check_password('testpass123'))
        self.assertEqual(user.role, User.Role.USER)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_superuser(self):
        admin = User.objects.create_superuser(
            email='admin@example.com',
            password='adminpass123'
        )
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)


class HouseAccountTests(TestCase):
    def test_only_one_user_can_be_flagged_as_house_account(self):
        User.objects.create_user(email='house1@example.com', password='testpass123', is_house_account=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(email='house2@example.com', password='testpass123', is_house_account=True)

    def test_multiple_users_can_have_is_house_account_false(self):
        # The conditional UniqueConstraint only applies when True - plenty
        # of ordinary users must coexist with is_house_account=False.
        User.objects.create_user(email='ordinary1@example.com', password='testpass123')
        User.objects.create_user(email='ordinary2@example.com', password='testpass123')
        self.assertEqual(User.objects.filter(is_house_account=False).count(), 2)

    def test_house_account_cannot_be_deleted_via_admin(self):
        from apps.accounts.admin import CustomUserAdmin
        from django.contrib import admin as django_admin

        house = User.objects.create_user(email='house3@example.com', password='testpass123', is_house_account=True)
        admin_instance = CustomUserAdmin(User, django_admin.site)
        # The house-account short-circuit returns False without touching
        # `request` at all, so a bare None request is safe to pass here.
        self.assertFalse(admin_instance.has_delete_permission(None, house))

    def test_bulk_delete_queryset_skips_house_account(self):
        from apps.accounts.admin import CustomUserAdmin
        from django.contrib import admin as django_admin

        house = User.objects.create_user(email='house4@example.com', password='testpass123', is_house_account=True)
        normal = User.objects.create_user(email='normal4@example.com', password='testpass123')
        admin_instance = CustomUserAdmin(User, django_admin.site)

        admin_instance.delete_queryset(None, User.objects.filter(pk__in=[house.pk, normal.pk]))

        self.assertTrue(User.objects.filter(pk=house.pk).exists())
        self.assertFalse(User.objects.filter(pk=normal.pk).exists())
