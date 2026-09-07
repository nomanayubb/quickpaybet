from django.contrib import admin

from .models import RewardPackage, UserRewardClaim


@admin.register(RewardPackage)
class RewardPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'deposit_threshold', 'commission_rate_bonus', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(UserRewardClaim)
class UserRewardClaimAdmin(admin.ModelAdmin):
    list_display = ('user', 'package', 'commission_rate_bonus_applied', 'deposit_total_at_claim', 'claimed_at')
    list_filter = ('package',)
    search_fields = ('user__email', 'package__name')
    date_hierarchy = 'claimed_at'
    readonly_fields = ('user', 'package', 'commission_rate_bonus_applied', 'deposit_total_at_claim', 'claimed_at')
