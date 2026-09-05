import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Wallet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('balance', models.DecimalField(decimal_places=8, default=0, max_digits=20)),
                ('reserved_balance', models.DecimalField(decimal_places=8, default=0, max_digits=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='wallet', to='accounts.user')),
            ],
            options={
                'verbose_name': 'Wallet',
                'verbose_name_plural': 'Wallets',
            },
        ),
        migrations.CreateModel(
            name='WalletTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('txn_type', models.CharField(choices=[('deposit', 'Deposit'), ('withdrawal', 'Withdrawal'), ('bet_placed', 'Bet Placed'), ('bet_won', 'Bet Won'), ('bet_refund', 'Bet Refund'), ('adjustment', 'Adjustment')], max_length=20)),
                ('amount', models.DecimalField(decimal_places=8, max_digits=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='pending', max_length=20)),
                ('balance_after', models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ('reference_id', models.CharField(blank=True, default='', max_length=255)),
                ('description', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transactions', to='wallet.wallet')),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Wallet Transaction',
                'verbose_name_plural': 'Wallet Transactions',
            },
        ),
    ]
