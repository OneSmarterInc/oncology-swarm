from pathlib import Path
from sqlalchemy import create_engine, event, select, inspect
from sqlalchemy.orm import sessionmaker
from app.models import Base, Control

class Database:
    def __init__(self, url):
        args = {}
        if url.startswith('sqlite:'):
            if url.startswith('sqlite:///') and ':memory:' not in url:
                Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
            args = {'check_same_thread': False, 'timeout': 30}
        self.engine = create_engine(url, connect_args=args, pool_pre_ping=True)
        if url.startswith('sqlite:'):
            @event.listens_for(self.engine, 'connect')
            def pragmas(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA busy_timeout=30000')
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def init_demo(self):
        if self.engine.dialect.name != 'sqlite':
            raise RuntimeError('PostgreSQL schema must be installed with alembic upgrade head')
        inspector=inspect(self.engine)
        if inspector.has_table('control') and 'run_number' not in {c['name'] for c in inspector.get_columns('control')}:
            raise RuntimeError('Existing v1 database needs the documented Alembic migration; create_all cannot upgrade existing tables')
        Base.metadata.create_all(self.engine)
        self.ensure_control()

    def ensure_control(self):
        if self.engine.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        elif self.engine.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError('Only SQLite demo and PostgreSQL are supported')
        with self.session.begin() as s:
            s.execute(insert(Control).values(id=1, paused=False, requests_used=0, jobs_created=0, llm_calls_used=0).on_conflict_do_nothing(index_elements=[Control.id]))
