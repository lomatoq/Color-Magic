"""GitHub release transport and transactional installation. No Blender API."""
import hashlib
import io
import json
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

REPO = 'lomatoq/Color-Magic'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'
LIMIT = 32 * 1024 * 1024


def version(tag):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', tag)
    if not match:
        raise ValueError('Invalid stable version')
    return tuple(map(int, match.groups()))


def download(url, limit=LIMIT):
    if not (url == API or url.startswith('https://github.com/' + REPO + '/releases/download/')):
        raise ValueError('Unexpected download URL')
    request = Request(url, headers={'User-Agent': 'Color-Prime-Updater', 'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'})
    with urlopen(request, timeout=25) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds size limit')
    return data


def latest():
    release = json.loads(download(API, 1024 * 1024))
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Not a stable release')
    number = version(release['tag_name'])
    name = 'Color_Prime_Studio_' + '.'.join(map(str, number)) + '_Universal.zip'
    assets = {a['name']: a['browser_download_url'] for a in release['assets']}
    return {'version': number, 'tag': release['tag_name'], 'zip': assets[name], 'checksum': assets[name + '.sha256']}


def fetch_package(release):
    data = download(release['zip'])
    digest = download(release['checksum'], 4096).decode('ascii').split()[0]
    if not re.fullmatch('[0-9a-fA-F]{64}', digest) or hashlib.sha256(data).hexdigest() != digest.lower():
        raise ValueError('SHA256 mismatch')
    validate(data, release['version'])
    return data


def validate(data, expected):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [i.filename for i in entries]
        if len(names) != len(set(names)) or sum(i.file_size for i in entries) > LIMIT:
            raise ValueError('Invalid archive size or duplicate entries')
        for item in entries:
            parts = item.filename.split('/')
            if len(parts) != 2 or parts[0] != 'color_prime' or parts[1] in ('', '.', '..') or '\\' in item.filename or ':' in item.filename:
                raise ValueError('Unsafe archive path')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlink in archive')
        manifest = json.loads(archive.read('color_prime/package_integrity.json'))
        if version(manifest['version']) != tuple(expected):
            raise ValueError('Release version mismatch')
        files = manifest['files']
        if '__init__.py' not in files or {n.split('/')[1] for n in names if n.endswith('.py')} != set(files):
            raise ValueError('Incomplete code manifest')
        for name, digest in files.items():
            if '/' in name or '\\' in name or hashlib.sha256(archive.read('color_prime/' + name)).hexdigest() != digest:
                raise ValueError('Package integrity mismatch')
    return True


def install(data, target, expected):
    """Stage beside the installed package; rollback if either rename fails."""
    validate(data, expected)
    target = Path(target).resolve()
    if not (target / '__init__.py').is_file():
        raise ValueError('Installation directory missing')
    backup_dir = target.parent / '.color_prime_backups'
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / ('color_prime-' + uuid.uuid4().hex + '.zip')
    with zipfile.ZipFile(backup, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in target.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path, 'color_prime/' + path.relative_to(target).as_posix())
    staging = Path(tempfile.mkdtemp(prefix='.color_prime_update_', dir=str(target.parent)))
    old = staging / 'previous'
    new = staging / 'next'
    new.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                (new / entry.filename.split('/')[1]).write_bytes(archive.read(entry))
        target.rename(old)
        try:
            new.rename(target)
        except Exception:
            old.rename(target)
            raise
    finally:
        # Never remove the only old installation if rollback itself failed.
        if target.is_dir():
            shutil.rmtree(staging)
    return str(backup)
