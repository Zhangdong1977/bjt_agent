"""Authentication module tests.

Test cases:
- AUTH-001: User registration success
- AUTH-002: Username duplicate
- AUTH-003: Email duplicate
- AUTH-004: Password too short
- AUTH-005: Login success
- AUTH-006: Login with wrong password
- AUTH-007: Token refresh
- AUTH-008: Get current user
"""

import uuid
import pytest
from httpx import AsyncClient


class TestAuthRegister:
    """Tests for user registration endpoint."""

    @pytest.mark.asyncio
    async def test_register_success(self, client: AsyncClient):
        """AUTH-001: User registration success."""
        user_data = {
            "username": f"test_user_{uuid.uuid4().hex[:8]}",
            "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Test123!",
        }

        response = await client.post("/api/auth/register", json=user_data)

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["username"] == user_data["username"]
        assert data["email"] == user_data["email"]
        assert "password" not in data
        assert "password_hash" not in data

    @pytest.mark.asyncio
    async def test_register_username_duplicate(self, client: AsyncClient):
        """AUTH-002: Username duplicate registration."""
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email1 = f"{username}_1@example.com"
        email2 = f"{username}_2@example.com"
        password = "Test123!"

        # First registration
        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email1, "password": password},
        )

        # Second registration with same username
        response = await client.post(
            "/api/auth/register",
            json={"username": username, "email": email2, "password": password},
        )

        assert response.status_code == 400
        assert "Username already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_email_duplicate(self, client: AsyncClient):
        """AUTH-003: Email duplicate registration."""
        username1 = f"test_user_{uuid.uuid4().hex[:8]}"
        username2 = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"same_email_{uuid.uuid4().hex[:8]}@example.com"
        password = "Test123!"

        # First registration
        await client.post(
            "/api/auth/register",
            json={"username": username1, "email": email, "password": password},
        )

        # Second registration with same email
        response = await client.post(
            "/api/auth/register",
            json={"username": username2, "email": email, "password": password},
        )

        assert response.status_code == 400
        assert "Email already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_password_too_short(self, client: AsyncClient):
        """AUTH-004: Password validation - too short."""
        user_data = {
            "username": f"test_user_{uuid.uuid4().hex[:8]}",
            "email": f"test_{uuid.uuid4().hex[:8]}@example.com",
            "password": "short",  # Less than 6 characters
        }

        response = await client.post("/api/auth/register", json=user_data)

        assert response.status_code == 422  # Validation error


class TestAuthLogin:
    """Tests for user login endpoint."""

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient):
        """AUTH-005: Login with correct credentials."""
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"

        # Register first
        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        # Login
        response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": password},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient):
        """AUTH-006: Login with wrong password."""
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"
        wrong_password = "WrongPassword!"

        # Register first
        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        # Login with wrong password
        response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": wrong_password},
        )

        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client: AsyncClient):
        """AUTH-006 variant: Login with non-existent user."""
        response = await client.post(
            "/api/auth/login",
            data={"username": "nonexistent_user", "password": "anypassword"},
        )

        assert response.status_code == 401


class TestAuthTokenRefresh:
    """Tests for token refresh endpoint."""

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, client: AsyncClient):
        """AUTH-007: Token refresh with valid refresh token."""
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"

        # Register and login
        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        login_response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": password},
        )
        refresh_token = login_response.json()["refresh_token"]

        # Refresh token
        response = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, client: AsyncClient):
        """AUTH-007: Token refresh with invalid token."""
        response = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": "invalid_token"},
        )

        assert response.status_code == 401


class TestAuthMe:
    """Tests for current user endpoint."""

    @pytest.mark.asyncio
    async def test_get_current_user_success(self, client: AsyncClient):
        """AUTH-008: Get current user with valid token."""
        username = f"test_user_{uuid.uuid4().hex[:8]}"
        email = f"{username}@example.com"
        password = "Test123!"

        # Register and login
        await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )

        login_response = await client.post(
            "/api/auth/login",
            data={"username": username, "password": password},
        )
        access_token = login_response.json()["access_token"]

        # Get current user
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == username
        assert data["email"] == email

    @pytest.mark.asyncio
    async def test_get_current_user_no_token(self, client: AsyncClient):
        """AUTH-008: Get current user without token."""
        response = await client.get("/api/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, client: AsyncClient):
        """AUTH-008: Get current user with invalid token."""
        response = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401
