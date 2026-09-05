from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_passwordresettoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='commission_rate',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=5,
                verbose_name='Commission rate (%) on losing bets of direct children',
            ),
        ),
    ]
