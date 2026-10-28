import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PIL import Image
from app.database import Database
from app.core import Settings

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path/'library.sqlite3')

@pytest.fixture
def photo(tmp_path):
    path=tmp_path/'portrait.png'
    Image.new('RGB',(800,1200),'#9b8bb3').save(path)
    return path

@pytest.fixture
def settings(tmp_path):
    settings=Settings(tmp_path/'user')
    # Existing permission/source tests explicitly exercise manual mode. The new
    # automatic/default workflow has separate integration tests.
    settings.values['automatic_search']=False
    return settings
