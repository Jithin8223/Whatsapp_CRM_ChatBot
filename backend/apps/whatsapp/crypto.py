import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings


def cipher():
    key = hashlib.sha256(('crm-provider-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value):
    return cipher().encrypt(value.encode()).decode() if value else ''


def decrypt(value):
    return cipher().decrypt(value.encode()).decode() if value else ''
