import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('sports', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('selection', models.CharField(choices=[('home', 'Home'), ('draw', 'Draw'), ('away', 'Away')], max_length=10)),
                ('odds', models.DecimalField(decimal_places=2, max_digits=10)),
                ('stake', models.DecimalField(decimal_places=8, max_digits=20)),
                ('potential_payout', models.DecimalField(decimal_places=8, max_digits=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('won', 'Won'), ('lost', 'Lost'), ('refunded', 'Refunded')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='bets', to='sports.match')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bets', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Bet',
                'verbose_name_plural': 'Bets',
            },
        ),
    ]
