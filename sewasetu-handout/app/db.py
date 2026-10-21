"""PostgreSQL connection setup for local and hosted PostgreSQL deployments."""

import configparser
import os
from pathlib import Path

import psycopg2


CONFIG_PATH = Path(__file__).resolve().parent / "config" / "app.ini"
config = configparser.ConfigParser()
config.read(CONFIG_PATH)


def connect_db():
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if database_url:
        connection_options = psycopg2.extensions.parse_dsn(database_url)
        connection_options.setdefault("sslmode", "require")
        connection_options.setdefault("connect_timeout", "10")
        return psycopg2.connect(**connection_options)

    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError(
            "Set DATABASE_URL for Neon or POSTGRES_PASSWORD for local PostgreSQL."
        )

    return psycopg2.connect(
        host=config.get("database", "host"),
        port=config.getint("database", "port"),
        dbname=config.get("database", "name"),
        user=config.get("database", "user"),
        password=password,
        connect_timeout=10,
    )
