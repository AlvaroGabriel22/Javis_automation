from alembic import context

from jarvis.adapters.persistence.db import new_engine
from jarvis.adapters.persistence.models import Base
from jarvis.bootstrap.settings import read_settings
from jarvis.modules.calendar import models as _calendar_models  # noqa: F401
from jarvis.modules.messaging import models as _messaging_models  # noqa: F401
from jarvis.modules.personal import models as _personal_models  # noqa: F401
from jarvis.modules.receipts import models as _receipt_models  # noqa: F401
from jarvis.modules.spreadsheets import export_models as _export_models  # noqa: F401
from jarvis.modules.spreadsheets import models as _sheet_models  # noqa: F401
from jarvis.modules.stickers import preferences as _sticker_preferences  # noqa: F401


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=read_settings().database_url(), target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
elif (existing := context.config.attributes.get("connection")) is not None:
    migrate(existing)
else:
    engine = new_engine()
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
