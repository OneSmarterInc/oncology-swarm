"""Opt-in real PostgreSQL integration. Creates/drops only a random test schema.

POSTGRES_TEST_URL must point to a dedicated test database owned by the test role.
No production data should be used. The default test suite skips these tests.
"""
import os
import uuid
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.engine import make_url
from alembic.config import Config
from alembic import command
from app.config import Settings
from app.db import Database
from app.models import Job, Proposal, Control
from app.service import Service
from app.worker import Coordinator
from app.cli import SEEDS

@pytest.mark.skipif(not os.getenv('POSTGRES_TEST_URL'), reason='No dedicated POSTGRES_TEST_URL configured')
def test_real_postgres_migration_leases_review_and_restart(monkeypatch):
    raw_url = os.environ['POSTGRES_TEST_URL']
    if not raw_url.startswith('postgresql+psycopg://'):
        pytest.fail('POSTGRES_TEST_URL must use postgresql+psycopg://')
    schema = 'swarm_test_' + uuid.uuid4().hex
    admin = create_engine(raw_url)
    with admin.begin() as c: c.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(raw_url).update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
    try:
        monkeypatch.setenv('DATABASE_URL', url)
        from app.config import get_settings
        get_settings.cache_clear()
        command.upgrade(Config('alembic.ini'), 'head')
        command.check(Config('alembic.ini'))
        settings = Settings(database_url=url, admin_token='postgres-test-' + 'x'*40)
        db = Database(url); db.ensure_control(); service = Service(db, settings)
        with ThreadPoolExecutor(max_workers=4) as pool:
            enqueued = list(pool.map(lambda _: service.enqueue(SEEDS[0]), range(8)))
        assert sum(created for _, created in enqueued) == 1
        service.enqueue(SEEDS[1]); Coordinator(service).run(once=True)
        with db.session() as s:
            assert all(j.state == 'review' for j in s.scalars(select(Job)))
            assert all(not p.validation_errors for p in s.scalars(select(Proposal)))
        service.review(enqueued[0][0], 1, 'PostgreSQL tester', 'Synthetic fixture verified', True)
        db.engine.dispose()
        restart = Database(url)
        with restart.session() as s:
            assert s.get(Job, enqueued[0][0]).state == 'approved'
            assert s.get(Control,1).jobs_created == 2
        restart.engine.dispose()
    finally:
        with admin.begin() as c: c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        get_settings.cache_clear()
