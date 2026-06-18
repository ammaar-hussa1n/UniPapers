from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from django.shortcuts import render
from django_ratelimit.decorators import ratelimit
from home.views import *

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/',include('allauth.urls')),
    path('', home, name='home'),
    path('login/',include('allauth.urls')),
    path('logout/', logout_page , name='logout'),
    path('privacy_policy/', privacy_policy , name='privacy_policy'),
    path('terms_and_conditions/', terms_and_conditions , name='terms_and_conditions'),

    path('search/', search, name='search'),
    path('view_paper/<int:paper_id>/<str:paper_title>/', view, name='view_paper'),
    path('view_paper/<int:paper_id>/<str:paper_title>/preview/', paper_preview, name='paper_preview'),
    path('delete_record/<int:paper_id>/', delete_record, name='delete_record'),
    path('delete_user/<int:user_id>/', delete_user, name='delete_user'),
    path('upload/', upload, name='upload'),
    path('about/', about, name='about'),
    path('profile/', profile, name='profile'),
]

handler404 = error_404_view
handler500 = error_500_view
handler403 = error_403_view
handler400 = error_400_view

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
