"""Forward upgrade preserves approved v1 rows; no user database is touched."""
from datetime import datetime
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, MetaData, select, inspect
from app.config import get_settings,Settings
from app.db import Database
from app.models import Doctor,DoctorSource,Proposal,Job
from app.service import Service
from app.fetcher import SafeFetcher
from app.extractor import extract
from app.cli import SEEDS

def test_upgrade_v1_approved_data_and_fresh_schema(tmp_path,monkeypatch):
    path=tmp_path/'old.db';url='sqlite:///'+str(path)
    monkeypatch.setenv('DATABASE_URL',url);get_settings.cache_clear()
    cfg=Config('alembic.ini')
    try:
        command.upgrade(cfg,'e271a68cbbf7')
        engine=create_engine(url);meta=MetaData();meta.reflect(engine)
        fetched=SafeFetcher(Settings(),lambda url:None).fetch(SEEDS[0]);data=extract(fetched);now=datetime(2026,10,19)
        with engine.begin() as c:
            c.execute(meta.tables['control'].insert().values(id=1,paused=False,requests_used=0,llm_calls_used=0,jobs_created=1,updated_at=now))
            c.execute(meta.tables['jobs'].insert().values(id=1,url=SEEDS[0],state='approved',llm_call_reserved=False,attempts=1,available_at=now,created_at=now,updated_at=now))
            c.execute(meta.tables['snapshots'].insert().values(id=1,job_id=1,url=fetched.url,text=fetched.text,content_hash=fetched.content_hash,retrieved_at=fetched.retrieved_at,synthetic=True))
            c.execute(meta.tables['proposals'].insert().values(id=1,job_id=1,data=data,validation_errors=[],duplicate_ids=[],version=2,reviewed_at=now,reviewer='V1 reviewer',review_note='Approved synthetic'))
            c.execute(meta.tables['doctors'].insert().values(id=1,proposal_id=1,name=data['doctor']['name'],normalized_name='asha rao',specialty=data['doctor']['specialty'],city=data['doctor']['city'],synthetic=True,approved_at=now))
        engine.dispose()
        command.upgrade(cfg,'head');command.check(cfg)
        db=Database(url);db.ensure_control();service=Service(db,Settings(database_url=url))
        with db.session() as s:
            doctor=s.get(Doctor,1);assert doctor.active and doctor.source_type=='synthetic'
            assert s.get(Job,1).host=='demo.example.invalid'
            assert s.get(Proposal,1).normalized_name=='asha rao'
            assert s.scalar(select(DoctorSource).where(DoctorSource.doctor_id==1)).proposal_id==1
        result=service.refresh_job(1,2,'V2 reviewer','Migration works')
        assert result['generation']==1
        with db.engine.connect() as c:
            assert not c.exec_driver_sql('PRAGMA foreign_key_check').all()
        db.engine.dispose()
    finally:get_settings.cache_clear()

def test_v1_createall_refuses_silent_upgrade(tmp_path,monkeypatch):
    import pytest
    url='sqlite:///'+str(tmp_path/'old.db')
    monkeypatch.setenv('DATABASE_URL',url);get_settings.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'),'e271a68cbbf7')
        with pytest.raises(RuntimeError,match='migration'):Database(url).init_demo()
    finally:get_settings.cache_clear()


def test_fresh_nested_sqlite_directory_created_by_migration(tmp_path,monkeypatch):
    path=tmp_path/'new'/'data'/'research.db'
    assert not path.parent.exists()
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(path));get_settings.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'),'head')
        command.check(Config('alembic.ini'))
        assert path.exists()
        engine=create_engine('sqlite:///'+str(path))
        with engine.connect() as c:
            assert c.exec_driver_sql('select version_num from alembic_version').scalar()=='b592cf2d0101'
        engine.dispose()
    finally:get_settings.cache_clear()
