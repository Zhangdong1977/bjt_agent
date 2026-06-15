"""Test configuration and fixtures."""

import sys
import uuid
from pathlib import Path

# Ensure project root is in sys.path for backend module imports
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.deps import create_access_token, get_password_hash
from backend.main import app
from backend.models import User, async_session_factory, engine

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="function")
def unique_user():
    """Generate unique user data for a test."""
    unique_id = uuid.uuid4().hex[:8]
    return {
        "username": f"test_user_{unique_id}",
        "email": f"test_{unique_id}@example.com",
        "password": "Test123!",
    }


@pytest_asyncio.fixture(scope="function")
async def client():
    """Create an in-process test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=30.0,
    ) as ac:
        yield ac
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def auth_headers(unique_user: dict) -> dict:
    """Create authenticated headers with a new local test user."""
    token = await _create_test_token(unique_user["username"], interior_user=False)

    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def interior_auth_headers() -> dict:
    """Create headers for a distinct interior (admin) user.

    Document tests are scoped to authorization behavior, not the external auth
    service. Create a separate local user and sign a server-valid token with
    ``interior_user=True`` so the API under test can load the user normally.
    """
    unique_id = uuid.uuid4().hex[:8]
    username = f"interior_user_{unique_id}"
    interior_token = await _create_test_token(username, interior_user=True)
    return {"Authorization": f"Bearer {interior_token}"}


async def _create_test_token(username: str, *, interior_user: bool) -> str:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@example.com",
                password_hash=get_password_hash("Test123!"),
            )
            session.add(user)
            await session.flush()
            await session.refresh(user)
        await session.commit()
    await engine.dispose()

    return create_access_token(
        data={"sub": user.id, "interior_user": interior_user, "concurrency": 2}
    )
