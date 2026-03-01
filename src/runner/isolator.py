from __future__ import annotations

import logging
import uuid

import mysql.connector

from src.db.connector import DatabaseConnector

logger = logging.getLogger(__name__)


class Isolator:
    """Provides database-level isolation for test suites.

    Each suite gets its own temporary database, ensuring no cross-suite
    data pollution. This is preferred over transaction-level isolation
    because TiDB DDL implicitly commits transactions.
    """

    def __init__(self, connector: DatabaseConnector):
        self._connector = connector

    def create_isolated_db(
        self, conn: mysql.connector.MySQLConnection, suite_name: str
    ) -> str:
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in suite_name)
        db_name = f"tidbtest_{safe_name}_{uuid.uuid4().hex[:8]}"
        result = self._connector.execute(conn, f"CREATE DATABASE `{db_name}`")
        if result.is_error:
            raise RuntimeError(f"Failed to create isolated database '{db_name}': {result.error}")
        result = self._connector.execute(conn, f"USE `{db_name}`")
        if result.is_error:
            raise RuntimeError(f"Failed to USE database '{db_name}': {result.error}")
        logger.info("Created isolated database: %s", db_name)
        return db_name

    def drop_isolated_db(
        self, conn: mysql.connector.MySQLConnection, db_name: str
    ) -> None:
        result = self._connector.execute(conn, f"DROP DATABASE IF EXISTS `{db_name}`")
        if result.is_error:
            logger.warning("Failed to drop database '%s': %s", db_name, result.error)
        else:
            logger.info("Dropped isolated database: %s", db_name)
