"""Private attachment storage and validation.

Storage backends (selected by ATTACHMENT_STORAGE):
  local     files under ATTACHMENT_ROOT (a private Docker volume; never served by nginx)
  supabase  a PRIVATE Supabase Storage bucket, accessed server-side with the service-role key
Files are always streamed through authenticated Django endpoints; no public or long-lived URLs.
"""
import hashlib
import io
import re
import uuid
import zipfile
from pathlib import Path, PurePath

import httpx
from django.conf import settings
from rest_framework.exceptions import ValidationError

# extension -> (kind, MIME). Executables, HTML, SVG and scripts are never accepted.
ALLOWED = {
    'jpg': ('image', 'image/jpeg'), 'jpeg': ('image', 'image/jpeg'), 'png': ('image', 'image/png'),
    'gif': ('image', 'image/gif'), 'webp': ('image', 'image/webp'),
    'pdf': ('pdf', 'application/pdf'),
    'docx': ('document', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    'doc': ('document', 'application/msword'),
    'xlsx': ('spreadsheet', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    'xls': ('spreadsheet', 'application/vnd.ms-excel'),
    'pptx': ('document', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
    'csv': ('text', 'text/csv'), 'txt': ('text', 'text/plain'),
}
OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
OOXML_PARTS = {'docx': 'word/', 'xlsx': 'xl/', 'pptx': 'ppt/'}


def max_bytes():
    return int(getattr(settings, 'ATTACHMENT_MAX_BYTES', 10 * 1024 * 1024))


def safe_filename(name):
    stem = PurePath(str(name or 'file')).name
    stem = re.sub(r'[^\w.\- ()]+', '_', stem).strip(' .') or 'file'
    return stem[-180:]


def _matches(ext, data):
    if ext in ('jpg', 'jpeg'):
        return data.startswith(b'\xff\xd8\xff')
    if ext == 'png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if ext == 'gif':
        return data[:6] in (b'GIF87a', b'GIF89a')
    if ext == 'webp':
        return data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    if ext == 'pdf':
        return data.startswith(b'%PDF-')
    if ext in ('doc', 'xls'):
        return data.startswith(OLE)
    if ext in OOXML_PARTS:
        if not data.startswith(b'PK\x03\x04'):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                if len(names) > 5000:
                    return False
                return '[Content_Types].xml' in names and any(n.startswith(OOXML_PARTS[ext]) for n in names) \
                    and not any(n.lower().endswith(('.bin', 'vbaproject.bin')) for n in names)  # no macros
        except (zipfile.BadZipFile, ValueError):
            return False
    if ext in ('csv', 'txt'):
        if b'\x00' in data:
            return False
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            return False
        return not re.search(rb'<\s*(script|html|svg|iframe)', data[:4096], re.I)
    return False


def inspect_upload(upload):
    """Validate an UploadedFile. Returns (filename, kind, mime, data)."""
    if upload is None:
        raise ValidationError({'file': 'Choose a file to send.'})
    if upload.size == 0:
        raise ValidationError({'file': 'The file is empty.'})
    if upload.size > max_bytes():
        raise ValidationError({'file': f'Files can be at most {max_bytes() // (1024 * 1024)} MB.'})
    name = safe_filename(upload.name)
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in ALLOWED:
        raise ValidationError({'file': 'Allowed files: images (JPG, PNG, GIF, WebP), PDF, Word, Excel, PowerPoint, CSV and text.'})
    data = upload.read(max_bytes() + 1)
    if len(data) > max_bytes():
        raise ValidationError({'file': f'Files can be at most {max_bytes() // (1024 * 1024)} MB.'})
    if not _matches(ext, data):
        raise ValidationError({'file': 'The file content does not match its type, or it contains macros. Save it as a standard file and retry.'})
    kind, mime = ALLOWED[ext]
    return name, kind, mime, data


class LocalPrivateStorage:
    name = 'local'

    def __init__(self, root=None):
        self.root = Path(root or settings.ATTACHMENT_ROOT)

    def _path(self, key):
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValidationError('Invalid attachment key.')
        return path

    def save(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'xb') as handle:  # never overwrite
            handle.write(data)

    def read(self, key):
        return self._path(key).read_bytes()

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class SupabaseStorage:
    """Private bucket via the Storage REST API using the server-only service-role key."""
    name = 'supabase'

    def __init__(self):
        self.base = settings.SUPABASE_URL.rstrip('/') + '/storage/v1/object'
        self.bucket = settings.SUPABASE_STORAGE_BUCKET
        self.headers = {'Authorization': f'Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}',
                        'apikey': settings.SUPABASE_SERVICE_ROLE_KEY}

    def _call(self, method, url, **kwargs):
        try:
            response = httpx.request(method, url, headers={**self.headers, **kwargs.pop('headers', {})},
                                     timeout=httpx.Timeout(30, connect=5), follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise ValidationError('Attachment storage is temporarily unavailable. Please retry.') from None
        if not response.is_success:
            raise ValidationError('Attachment storage rejected the request.')
        return response

    def save(self, key, data, content_type):
        self._call('POST', f'{self.base}/{self.bucket}/{key}', content=data,
                   headers={'Content-Type': content_type, 'x-upsert': 'false'})

    def read(self, key):
        return self._call('GET', f'{self.base}/authenticated/{self.bucket}/{key}').content

    def delete(self, key):
        self._call('DELETE', f'{self.base}/{self.bucket}/{key}')


def storage(name=None):
    name = name or settings.ATTACHMENT_STORAGE
    if name == 'supabase':
        return SupabaseStorage()
    return LocalPrivateStorage()


def store(business_id, conversation_id, upload):
    """Validate and store an upload; returns the attachment field values."""
    filename, kind, mime, data = inspect_upload(upload)
    backend = storage()
    key = f'{business_id}/{conversation_id}/{uuid.uuid4().hex}'
    backend.save(key, data, mime)
    return {'storage_backend': backend.name, 'storage_key': key, 'filename': filename, 'kind': kind,
            'content_type': mime, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def read(attachment):
    data = storage(attachment.storage_backend).read(attachment.storage_key)
    if hashlib.sha256(data).hexdigest() != attachment.sha256:
        raise ValidationError('The stored file failed its integrity check.')
    return data
