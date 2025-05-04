"""
URL configuration for hamsukypay project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    # Django auth URLs
    path('accounts/login/', auth_views.LoginView.as_view(redirect_field_name='next', template_name='payments/merchant_login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    # Password reset URLs (Django built-in)
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='payments/password_reset.html',
        email_template_name='payments/emails/password_reset_email.html',
        subject_template_name='payments/emails/password_reset_subject.txt',
        success_url='/password-reset/done/'
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='payments/password_reset_done.html'
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='payments/password_reset_confirm.html',
        success_url='/password-reset-complete/'
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='payments/password_reset_complete.html'
    ), name='password_reset_complete'),
    
    # Main app URLs
    path('', include('payments.urls', namespace='payments')),
    # Comment out the redirect since it conflicts with the root URL pattern
    # path('', RedirectView.as_view(pattern_name='api-root', permanent=False)),
]
