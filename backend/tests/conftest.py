"""Test configuration and fixtures."""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient

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
    """Create test client that connects to running server."""
    async with AsyncClient(base_url=BASE_URL, timeout=30.0) as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def auth_headers(client: AsyncClient, unique_user: dict) -> dict:
    """Create authenticated headers with a new test user."""
    # Register user
    await client.post(
        "/api/auth/register",
        json=unique_user,
    )

    # Login
    response = await client.post(
        "/api/auth/login",
        data={"username": unique_user["username"], "password": unique_user["password"]},
    )
    token = response.json()["access_token"]

    return {"Authorization": f"Bearer {token}"}
