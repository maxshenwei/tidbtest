from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

RETRYABLE_MYSQL_CODES = {
    2006,  # MySQL server has gone away
    2013,  # Lost connection to MySQL server during query
    1205,  # Lock wait timeout exceeded
    1213,  # Deadlock found when trying to get lock
    4031,  # The client was disconnected by the server because of inactivity (TiDB)
}

NON_RETRYABLE_MYSQL_CODES = {
    1064,  # Syntax error
    1146,  # Table doesn't exist
    1054,  # Unknown column
    1062,  # Duplicate entry
    1048,  # Column cannot be null
    1452,  # Cannot add or update a child row (FK)
}


class Retrier:
    """Handles retry logic for transient infrastructure failures.

    Only retries infra-level errors (connection lost, lock timeout).
    Never retries assertion failures or SQL syntax errors.
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.max_retries: int = config.get("max_retries", 2)
        self.backoff_base: float = config.get("backoff_base", 1.0)
        self.max_backoff: float = config.get("max_backoff", 10.0)

    def is_retryable(self, error: Exception) -> bool:
        errno = getattr(error, "errno", None)
        if errno is not None:
            if errno in NON_RETRYABLE_MYSQL_CODES:
                return False
            if errno in RETRYABLE_MYSQL_CODES:
                return True

        err_msg = str(error).lower()
        retryable_patterns = [
            "connection",
            "lost",
            "gone away",
            "timeout",
            "deadlock",
            "try restarting transaction",
        ]
        return any(p in err_msg for p in retryable_patterns)

    def wait(self, attempt: int) -> None:
        delay = min(self.backoff_base * (2 ** (attempt - 1)), self.max_backoff)
        logger.info("Retry attempt %d, waiting %.1fs", attempt, delay)
        time.sleep(delay)
