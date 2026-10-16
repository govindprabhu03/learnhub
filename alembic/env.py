import os
import sys

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from config import SA_URL  # noqa: E402

if context.is_offline_mode():
    context.configure(url=SA_URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(SA_URL, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()
