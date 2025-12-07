import pytest
from gabs_api_server.app import app as flask_app, limiter # Import the limiter instance
import sqlite3
from gabs_api_server import database
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

@pytest.fixture
def app(monkeypatch): # Add monkeypatch as argument
    monkeypatch.setenv("WEBSITE_URL", "https://test.example.com/") # Set the env var
    flask_app.config["TESTING"] = True
    flask_app.config["JWT_SECRET_KEY"] = "test-secret-key" # Used for testing JWTs
    
    # --- RECONFIGURE THE EXISTING LIMITER INSTANCE ---
    # Re-initialize the limiter with testing-friendly limits
    # The actual Limiter instance is already created in app.py at module load.
    # We can re-init it with different settings for testing.
    limiter.init_app(flask_app) # Re-bind to the app context if needed
    limiter.enabled = False # Disable it completely for tests
    # -------------------------------------------------

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
    monkeypatch.setattr('gabs_api_server.app.jobstores', jobstores)

    conn = sqlite3.connect(db_uri)
    database.init_db()
    yield conn
    conn.close()