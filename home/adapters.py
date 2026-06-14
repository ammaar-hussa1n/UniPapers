from django.contrib import messages
from django.shortcuts import redirect

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class UniversitySocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        email = self._get_email_address(sociallogin)

        if not email or not email.lower().endswith('.edu.pk'):
            if request is not None:
                messages.error(request, 'Only University email IDs ending with .edu.pk are allowed.')
            raise ImmediateHttpResponse(redirect('account_login'))

        return super().pre_social_login(request, sociallogin)

    def _get_email_address(self, sociallogin):
        if sociallogin.user and sociallogin.user.email:
            return sociallogin.user.email.strip()

        extra_data = getattr(sociallogin.account, 'extra_data', {}) or {}
        email = extra_data.get('email')
        if email:
            return email.strip()

        emails = extra_data.get('emails') or []
        if emails:
            first_email = emails[0]
            if isinstance(first_email, dict):
                return (first_email.get('value') or '').strip()

        return ''