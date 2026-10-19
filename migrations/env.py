from alembic import context
from pathlib import Path
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from app.config import get_settings
from app.models import Base

config = context.config
url = get_settings().database_url
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction(): context.run_migrations()
else:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in {None, "", ":memory:"}:
        # Fresh extracted releases intentionally contain no data directory.
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()
