from dataclasses import replace
import pytest
from app.config import Settings
from app.db import Database
from app.service import Service

@pytest.fixture
def stack(tmp_path):
    settings = Settings(database_url='sqlite:///' + str(tmp_path / 'test.db'), admin_token='test-token-' + 'a'*40)
    db = Database(settings.database_url); db.init_demo()
    yield settings, db, Service(db, settings)
    db.engine.dispose()
