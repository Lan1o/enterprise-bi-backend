from alembic import context

from bi_backend import models
from bi_backend.config import Settings
from bi_backend.db import make_database

config = context.config
url = Settings().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=models.Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = make_database(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=models.Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
