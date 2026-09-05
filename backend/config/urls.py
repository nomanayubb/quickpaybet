from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.accounts.urls')),
    path('api/', include('apps.wallet.urls')),
    path('api/', include('apps.sports.urls')),
    path('api/', include('apps.bets.urls')),
    path('api/', include('apps.reports.urls')),
    path('api/payments/', include('apps.payments.urls')),
]
