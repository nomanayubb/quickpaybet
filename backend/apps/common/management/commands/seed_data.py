from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.sports.models import Sport, Tournament, Match


class Command(BaseCommand):
    help = 'Create demo users, sports, tournaments and matches for development.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting development seed...'))

        # ------------------------------------------------------------
        # Create demo users
        # ------------------------------------------------------------
        admin_email = 'admin@quickpaybet.com'
        master_email = 'master@quickpaybet.com'
        agent_email = 'agent@quickpaybet.com'
        user_email = 'user@quickpaybet.com'
        password = 'QuickPayBet123!'

        admin, admin_created = User.objects.get_or_create(
            email=admin_email,
            defaults={
                'is_staff': True,
                'is_superuser': True,
                'role': User.Role.ADMIN,
            },
        )
        if admin_created:
            admin.set_password(password)
            admin.save()
            self.stdout.write(f'  Created admin: {admin_email} / {password}')

        master, master_created = User.objects.get_or_create(
            email=master_email,
            defaults={
                'role': User.Role.MASTER,
                'parent': admin,
                'min_bet_amount': '10',
                'max_bet_amount': '5000',
            },
        )
        if master_created:
            master.set_password(password)
            master.save()
            self.stdout.write(f'  Created master: {master_email}')

        agent, agent_created = User.objects.get_or_create(
            email=agent_email,
            defaults={
                'role': User.Role.AGENT,
                'parent': master,
                'min_bet_amount': '1',
                'max_bet_amount': '1000',
            },
        )
        if agent_created:
            agent.set_password(password)
            agent.save()
            self.stdout.write(f'  Created agent: {agent_email}')

        user, user_created = User.objects.get_or_create(
            email=user_email,
            defaults={
                'role': User.Role.USER,
                'parent': agent,
                'min_bet_amount': '0.01',
                'max_bet_amount': '100',
            },
        )
        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(f'  Created user: {user_email}')

        # ------------------------------------------------------------
        # Create Football sport and sample tournament
        # ------------------------------------------------------------
        football, football_created = Sport.objects.get_or_create(
            slug='football',
            defaults={'name': 'Football'},
        )
        if football_created:
            self.stdout.write('  Created sport: Football')

        epl, epl_created = Tournament.objects.get_or_create(
            sport=football,
            name='English Premier League',
            season='2025/26',
        )
        if epl_created:
            self.stdout.write('  Created tournament: English Premier League')

        # ------------------------------------------------------------
        # Create several sample matches
        # ------------------------------------------------------------
        now = timezone.now()
        days_ahead = [1, 2, 3]
        teams = [
            ('Arsenal', 'Chelsea', '2.20', '3.40', '3.10'),
            ('Liverpool', 'Man City', '2.60', '3.20', '2.80'),
            ('Man United', 'Tottenham', '2.40', '3.35', '3.00'),
            ('Newcastle', 'Aston Villa', '1.95', '3.60', '3.90'),
        ]

        created_matches = 0
        for i, (home, away, odds_home, odds_draw, odds_away) in enumerate(teams):
            start = now + timedelta(days=days_ahead[i % len(days_ahead)], hours=i)
            _, was_created = Match.objects.get_or_create(
                sport=football,
                tournament=epl,
                home_team=home,
                away_team=away,
                start_time=start,
                defaults={
                    'odds_home': odds_home,
                    'odds_draw': odds_draw,
                    'odds_away': odds_away,
                },
            )
            if was_created:
                created_matches += 1

        self.stdout.write(self.style.SUCCESS(
            f'Created {created_matches} sample match(es).'
        ))
        self.stdout.write(self.style.SUCCESS('Development seed complete.'))
