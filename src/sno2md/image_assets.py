"""Lossless, content-addressed images for portable Markdown document folders."""
import base64
import hashlib
from pathlib import Path
import re
from urllib.parse import quote

from .model import RenderError

DATA_IMAGE = re.compile(r'data:image/([A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)')
EXTENSIONS = {'png': 'png', 'jpeg': 'jpg', 'jpg': 'jpg', 'gif': 'gif', 'webp': 'webp'}
ASSET_NAME = re.compile(r'[0-9a-f]{64}\.(?:png|jpg|gif|webp)\Z')


def asset_folder(name, document_folders=True):
    return 'images' if document_folders else name + '.assets'


def externalize_images(markdown, destination, *, folder='images'):
    """Only replace data URIs; retain every image occurrence and all other text."""
    destination = Path(destination)
    if not folder or folder in {'.', '..'} or any(c in folder for c in '/\\:'):
        raise ValueError('image folder must be a filename component')
    assets = {}
    def replace(match):
        extension = EXTENSIONS.get(match[1].lower())
        if extension is None:
            raise RenderError(f'unsupported embedded image type: {match[1]}')
        try:
            data = base64.b64decode(''.join(match[2].split()), validate=True)
        except ValueError as error:
            raise RenderError('invalid embedded image data') from error
        fingerprint = hashlib.sha256(data).hexdigest()
        relative = folder + '/' + fingerprint + '.' + extension
        if relative not in assets:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # A staging directory is caller-owned. Never replace existing bytes.
            with path.open('xb') as stream:
                stream.write(data)
            assets[relative] = fingerprint
        return quote(relative, safe='/')
    result = DATA_IMAGE.sub(replace, markdown)
    if re.search(r'data:image/[^\s;]+;base64,', result):
        raise RenderError('embedded image could not be externalized')
    return result, assets


def checked_asset_path(parent, relative, folder):
    """Validate a manifest path, including every existing parent on Windows."""
    parts = relative.split('/') if isinstance(relative, str) else []
    if len(parts) != 2 or parts[0] != folder or not ASSET_NAME.fullmatch(parts[1]):
        raise ValueError('invalid managed image path')
    parent = Path(parent).absolute()
    for path in (parent, parent / folder):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('managed image parent is a symlink or junction')
    path = parent / relative
    if not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError('managed image escapes document folder')
    return path
