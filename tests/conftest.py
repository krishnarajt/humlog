"""Test fixtures: an in-memory database, a writer, and a post to hang blocks off."""

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Import the app package without needing a real database or a real secret.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-not-used-anywhere-real")

from app.db.database import Base  # noqa: E402
from app.db.models import Block, Blog, Comment, Image, User  # noqa: E402,F401
from app.services import blog_service  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def user(db):
    user = User(
        username="tester",
        email="tester@example.com",
        display_name="Tester",
        password_hash="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def blog(db, user):
    return blog_service.create_blog(db, user, title="A post about typing")


@pytest.fixture()
def contents(db):
    """A post's blocks as `(depth, content)`, in reading order.

    Almost every assertion in the block tests is about shape and order, and
    this is the shortest way to write one down.
    """
    from app.services.block_service import list_blocks

    def read(blog_id: int) -> list[tuple[int, str]]:
        return [(depth, block.content) for block, depth in list_blocks(db, blog_id)]

    return read


@pytest.fixture()
def client(db):
    """A TestClient wired to the same in-memory database as the `db` fixture.

    The rate limiter's counters are per-process and would otherwise leak
    between tests — a test that signs in eight times would make the next one
    fail — so they are cleared alongside the database.
    """
    from fastapi.testclient import TestClient

    from app.api import rate_limit
    from app.db.database import get_db
    from main import app

    rate_limit._requests.clear()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def author(client):
    """A registered writer, and a client that sends their token on every call."""
    response = client.post(
        "/api/auth/register",
        json={
            "username": "writer",
            "email": "writer@example.com",
            "password": "hunter2",
            "display_name": "A Writer",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return response.json()["user"]
