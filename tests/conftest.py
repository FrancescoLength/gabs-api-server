import pytest
from app import app as flask_app
import sqlite3
import database
import tempfile
import os
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

@pytest.fixture
def app():
    yield flask_app

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def memory_db(monkeypatch, tmp_path, app):
    db_file = tmp_path / "test.db"
    db_uri = str(db_file)
    monkeypatch.setattr(database, "DATABASE_FILE", db_uri)
    
    # Ensure the scheduler also uses this database
    jobstores = {'default': SQLAlchemyJobStore(url=f'sqlite:///{db_uri}')}
    monkeypatch.setattr('app.jobstores', jobstores)

    conn = sqlite3.connect(db_uri)
    database.init_db()
    yield conn
    conn.close()
