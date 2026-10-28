import json
import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

APP_NAME = 'K-IDOL ARCHIVE'


def data_root():
    override = os.environ.get('K_IDOL_DATA_DIR')
    return Path(override) if override else Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'K-IDOL-ARCHIVE'


class Settings:
    defaults = dict(thumbnail_size=216, sort='newest', sidebar=True, info=True,
                    viewer_mode='fit', theme='dark', cache_path='', storage_path='')

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'settings.json'
        self.values = self.defaults.copy()
        try:
            value = json.loads(self.path.read_text('utf-8'))
            if isinstance(value, dict):
                self.values.update(value)
        except (OSError, ValueError):
            pass
        for key in ('sidebar', 'info'):
            if not isinstance(self.values[key], bool):
                self.values[key] = self.defaults[key]
        size = self.values.get('thumbnail_size')
        if not isinstance(size, int) or not 128 <= size <= 384:
            self.values['thumbnail_size'] = 216
        if self.values['sort'] not in ('newest', 'oldest', 'name', 'resolution'):
            self.values['sort'] = 'newest'
        for key in ('cache_path', 'storage_path'):
            if not isinstance(self.values[key], str):
                self.values[key] = ''
        if self.values['viewer_mode'] not in ('fit', 'actual'):
            self.values['viewer_mode'] = 'fit'

    def save(self):
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as f:
            json.dump(self.values, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(self.path)

    @property
    def cache(self):
        return Path(self.values['cache_path'] or self.root / 'thumbnails')

    @property
    def storage(self):
        return Path(self.values['storage_path'] or self.root / 'originals')


def configure_logging(root):
    path = Path(root) / 'logs'
    path.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path / 'app.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s', force=True)
