from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sports', '0001_initial'),
        ('bets', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ParlayBet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stake', models.DecimalField(decimal_places=8, max_digits=20)),
                ('total_odds', models.DecimalField(decimal_places=2, max_digits=10)),
                ('potential_payout', models.DecimalField(decimal_places=8, max_digits=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('won', 'Won'), ('lost', 'Lost'), ('refunded', 'Refunded')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parlay_bets', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Parlay Bet',
                'verbose_name_plural': 'Parlay Bets',
            },
        ),
        migrations.CreateModel(
            name='ParlayLeg',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('selection', models.CharField(choices=[('home', 'Home'), ('draw', 'Draw'), ('away', 'Away')], max_length=10)),
                ('odds', models.DecimalField(decimal_places=2, max_digits=10)),
                ('outcome', models.CharField(choices=[('pending', 'Pending'), ('won', 'Won'), ('lost', 'Lost'), ('refunded', 'Refunded')], default='pending', max_length=20)),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='parlay_legs', to='sports.match')),
                ('parlay', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='legs', to='bets.parlaybet')),
            ],
            options={
                'verbose_name': 'Parlay Leg',
                'verbose_name_plural': 'Parlay Legs',
            },
        ),
    ]
