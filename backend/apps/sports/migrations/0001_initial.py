import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Sport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
                'verbose_name': 'Sport',
                'verbose_name_plural': 'Sports',
            },
        ),
        migrations.CreateModel(
            name='Tournament',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('season', models.CharField(blank=True, default='', max_length=50)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sport', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tournaments', to='sports.sport')),
            ],
            options={
                'ordering': ['-created_at'],
                'unique_together': {('sport', 'name', 'season')},
                'verbose_name': 'Tournament',
                'verbose_name_plural': 'Tournaments',
            },
        ),
        migrations.CreateModel(
            name='Match',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('home_team', models.CharField(max_length=150)),
                ('away_team', models.CharField(max_length=150)),
                ('start_time', models.DateTimeField()),
                ('status', models.CharField(choices=[('scheduled', 'Scheduled'), ('live', 'Live'), ('finished', 'Finished'), ('cancelled', 'Cancelled')], default='scheduled', max_length=20)),
                ('home_score', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('away_score', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('odds_home', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('odds_draw', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('odds_away', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sport', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='matches', to='sports.sport')),
                ('tournament', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='matches', to='sports.tournament')),
            ],
            options={
                'ordering': ['start_time'],
                'verbose_name': 'Match',
                'verbose_name_plural': 'Matches',
            },
        ),
    ]
