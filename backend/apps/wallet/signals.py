from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Wallet


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_wallet_for_new_user(sender, instance, created, **kwargs):
    # raw=True means this save came from loaddata (e.g. restore_database) -
    # the fixture already carries its own Wallet row for this user, so
    # auto-creating one here would collide with it on the OneToOne constraint.
    if kwargs.get('raw'):
        return
    if created:
        Wallet.objects.get_or_create(user=instance)
