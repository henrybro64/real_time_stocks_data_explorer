import logging

from app.core.logging import configure_logging
from app.db.init_db import seed_default_user

configure_logging()
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    seed_default_user()
    logger.info(
        "Seeded default user",
        extra={"event": "scripts.seed_default_user.completed", "user_id": "default-user"},
    )
