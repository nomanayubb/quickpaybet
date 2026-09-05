import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CryptoPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=8, max_digits=20)),
                ('currency', models.CharField(default='USDT', max_length=20)),
                ('payment_type', models.CharField(choices=[('deposit', 'Deposit'), ('withdrawal', 'Withdrawal')], default='deposit', max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('completed', 'Completed'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('provider', models.CharField(default='mock', max_length=100)),
                ('external_id', models.CharField(max_length=255, unique=True)),
                ('address', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='crypto_payments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Crypto Payment',
                'verbose_name_plural': 'Crypto Payments',
            },
        ),
    ]
