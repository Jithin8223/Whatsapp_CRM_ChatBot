import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def mobile_cipher():
    key = hashlib.sha256(('crm-guest-mobile-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def email_cipher():
    key = hashlib.sha256(('crm-guest-email-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))
