from __future__ import annotations

import logging
import os

import mysql.connector
from mysql.connector import Error as MySQLError

from src.models import ExecuteResult

logger = logging.getLogger(__name__)


class DatabaseConnector:
    """Manages connections to a TiDB/MySQL instance."""

    def __init__(self, config: dict):
        self._config = {
            "host": config.get("host", "127.0.0.1"),
            "port": config.get("port", 4000),
            "user": config.get("user", "root"),
            "password": config.get("password", ""),
            "autocommit": True,
            "connection_timeout": config.get("connection_timeout", 10),
        }

    def get_connection(self) -> mysql.connector.MySQLConnection:
        return mysql.connector.connect(**self._config)

    def execute(self, conn: mysql.connector.MySQLConnection, sql: str) -> ExecuteResult:
        cursor = conn.cursor()
        try:
            cursor.execute(sql)
            if cursor.description:
                rows = [list(row) for row in cursor.fetchall()]
                columns = [desc[0] for desc in cursor.description]
                return ExecuteResult(rows=rows, column_names=columns)
            return ExecuteResult(affected_rows=cursor.rowcount)
        except MySQLError as e:
            return ExecuteResult(error=e)
        finally:
            cursor.close()

    def execute_many(
        self, conn: mysql.connector.MySQLConnection, sqls: list[str]
    ) -> list[ExecuteResult]:
        results = []
        for sql in sqls:
            result = self.execute(conn, sql)
            results.append(result)
            if result.is_error:
                logger.warning("SQL failed during batch: %s -> %s", sql, result.error)
        return results

    def get_version(self, conn: mysql.connector.MySQLConnection) -> str:
        result = self.execute(conn, "SELECT VERSION()")
        if result.rows:
            return str(result.rows[0][0])
        return "unknown"


def load_connector_from_config(config: dict) -> DatabaseConnector:
    db_conf = config.get("database", {})
    password = db_conf.get("password", "")
    if password.startswith("${") and password.endswith("}"):
        env_var = password[2:-1]
        password = os.environ.get(env_var, "")
        db_conf["password"] = password
    return DatabaseConnector(db_conf)
