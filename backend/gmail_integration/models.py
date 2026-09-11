"""
gmail_integration/models.py
────────────────────────────
One row per user who has connected their own Gmail account for sending.

The refresh token is the only long-lived secret here, so it is the only field
encrypted at rest, via Fernet under GMAIL_TOKEN_ENCRYPTION_KEY (see
encrypt_refresh_token / decrypt_refresh_token below). access_token is a
short-lived cache the API refreshes on demand and is not worth encrypting.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "GMAIL_TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        raise ImproperlyConfigured(
            "GMAIL_TOKEN_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it in the "
            "environment before connecting a Gmail account."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_refresh_token(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_refresh_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


class GmailAccount(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gmail_account",
    )
    google_email = models.EmailField()
    refresh_token_encrypted = models.TextField()
    access_token = models.TextField(blank=True, default="")
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True, default="")
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "gmail_accounts"

    def __str__(self):
        return f"{self.user} <{self.google_email}>"

    def set_refresh_token(self, raw):
        self.refresh_token_encrypted = encrypt_refresh_token(raw)

    def get_refresh_token(self):
        return decrypt_refresh_token(self.refresh_token_encrypted)
