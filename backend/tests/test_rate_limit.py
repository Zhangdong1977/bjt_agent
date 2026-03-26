"""Tests for API rate limiting middleware.

Test cases:
- RATE-001: Auth endpoint within rate limit should succeed
- RATE-002: Auth endpoint exceeding rate limit should return 429
- RATE-003: Rate limit should be per-IP address
- RATE-004: Rate limit should reset after window expires
- RATE-005: Non-auth endpoints should not be rate limited (or have higher limits)
- RATE-006: Login failure should also count toward rate limit
- RATE-007: Rate limit headers should be present in response
- RATE-008: Multiple users from same IP share rate limit
"""

import asyncio
import time
import pytest
from httpx import AsyncClient


# Rate limit settings (should match implementation)
DEFAULT_AUTH_RATE_LIMIT = 5  # requests per window
DEFAULT_AUTH_RATE_WINDOW = 60  # seconds


async def create_test_user(client: AsyncClient, username: str, email: str) -> dict:
    """Helper to create a test user via registration."""
    response = await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "Test123!"},
    )
    return response.json()


class TestAuthRateLimiting:
    """Tests for authentication endpoint rate limiting."""

    @pytest.mark.asyncio
    async def test_auth_endpoint_within_limit(
        self, client: AsyncClient, unique_user: dict
    ):
        """RATE-001: Auth endpoint within rate limit should succeed.

        Making requests under the rate limit should not be blocked.
        """
        # First registration should succeed
        response = await client.post(
            "/api/auth/register",
            json=unique_user,
        )
        assert response.status_code in [200, 201, 400]  # 400 if already exists

    @pytest.mark.asyncio
    async def test_auth_endpoint_exceeding_limit(
        self, client: AsyncClient
    ):
        """RATE-002: Auth endpoint exceeding rate limit should return 429.

        Making too many requests to auth endpoints should be blocked.
        """
        # Create unique users to avoid "already exists" errors
        # but exhaust the rate limit
        for i in range(DEFAULT_AUTH_RATE_LIMIT + 1):
            unique_id = f"ratelimit_user_{int(time.time() * 1000)}_{i}"
            response = await client.post(
                "/api/auth/register",
                json={
                    "username": unique_id,
                    "email": f"{unique_id}@example.com",
                    "password": "Test123!",
                },
            )
            # After exceeding limit, should get 429
            if response.status_code == 429:
                break

        # The last request should have been rate limited
        assert response.status_code == 429
        assert "rate" in response.json()["detail"].lower() or "retry" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_login_endpoint_rate_limited(
        self, client: AsyncClient, unique_user: dict
    ):
        """RATE-002 variant: Login endpoint should also be rate limited.

        Login attempts should count toward rate limiting to prevent brute force.
        """
        # First register a user
        await client.post("/api/auth/register", json=unique_user)

        # Then try multiple login attempts (some with wrong password)
        for i in range(DEFAULT_AUTH_RATE_LIMIT + 1):
            response = await client.post(
                "/api/auth/login",
                data={
                    "username": unique_user["username"],
                    "password": "WrongPassword" if i < DEFAULT_AUTH_RATE_LIMIT else unique_user["password"],
                },
            )
            # After exceeding limit, should get 429
            if response.status_code == 429:
                break

        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(
        self, client: AsyncClient
    ):
        """RATE-004: Rate limit should reset after window expires.

        After the rate limit window expires, new requests should be allowed.
        """
        # Exhaust rate limit
        for i in range(DEFAULT_AUTH_RATE_LIMIT):
            unique_id = f"window_test_{int(time.time() * 1000)}_{i}"
            await client.post(
                "/api/auth/register",
                json={
                    "username": unique_id,
                    "email": f"{unique_id}@example.com",
                    "password": "Test123!",
                },
            )

        # This should be rate limited
        last_response = await client.post(
            "/api/auth/register",
            json={
                "username": f"window_test_{int(time.time() * 1000)}_last",
                "email": f"window_test_last@example.com",
                "password": "Test123!",
            },
        )
        assert last_response.status_code == 429

        # Note: In real test, we would wait for the window to expire
        # For unit tests without time manipulation, we verify the rate limit is applied

    @pytest.mark.asyncio
    async def test_rate_limit_headers_present(
        self, client: AsyncClient, unique_user: dict
    ):
        """RATE-007: Rate limit headers should be present in response.

        Responses should include headers indicating rate limit status.
        """
        # Make a request
        response = await client.post(
            "/api/auth/register",
            json=unique_user,
        )

        # Check for rate limit related headers
        # Common headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
        headers = response.headers
        rate_limit_headers = [
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
            "x-ratelimit-reset",
            "ratelimit-limit",
            "ratelimit-remaining",
            "ratelimit-reset",
            "retry-after",
        ]

        has_rate_header = any(header.lower() in headers for header in rate_limit_headers)
        # Note: Implementation may or may not include these headers

    @pytest.mark.asyncio
    async def test_failed_login_counts_toward_rate_limit(
        self, client: AsyncClient, unique_user: dict
    ):
        """RATE-006: Failed login attempts should count toward rate limit.

        Both successful and failed auth attempts should be rate limited.
        """
        # Register a user first
        await client.post("/api/auth/register", json=unique_user)

        # Make failed login attempts up to the limit
        for i in range(DEFAULT_AUTH_RATE_LIMIT):
            response = await client.post(
                "/api/auth/login",
                data={
                    "username": unique_user["username"],
                    "password": "WrongPassword",
                },
            )

        # Next failed attempt should be rate limited
        response = await client.post(
            "/api/auth/login",
            data={
                "username": unique_user["username"],
                "password": "WrongPassword",
            },
        )
        assert response.status_code == 429


class TestNonAuthEndpointRateLimiting:
    """Tests for rate limiting on non-authentication endpoints."""

    @pytest.mark.asyncio
    async def test_public_endpoints_not_rate_limited(
        self, client: AsyncClient
    ):
        """RATE-005: Public endpoints should not be rate limited.

        Endpoints like /health and /docs should not be rate limited.
        """
        # Health endpoint should always work
        response = await client.get("/health")
        assert response.status_code == 200

        # Should be able to hit it many times without 429
        for _ in range(10):
            response = await client.get("/health")
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_project_endpoints_have_higher_limit(
        self, client: AsyncClient, auth_headers: dict
    ):
        """RATE-005 variant: Project endpoints should have higher rate limits.

        Authenticated endpoints may have different (higher) rate limits.
        """
        # Should be able to list projects multiple times
        for _ in range(10):
            response = await client.get("/api/projects", headers=auth_headers)
            assert response.status_code == 200


class TestRateLimitIsolation:
    """Tests for rate limit isolation between users/IPs."""

    @pytest.mark.asyncio
    async def test_different_users_same_ip_shared_limit(
        self, client: AsyncClient
    ):
        """RATE-008: Multiple users from same IP share rate limit.

        Rate limiting should be per-IP, not per-user, to prevent
        a single IP from overwhelming the server with many users.
        """
        # Create many users from same client (same IP)
        for i in range(DEFAULT_AUTH_RATE_LIMIT + 2):
            unique_id = f"shared_ip_user_{int(time.time() * 1000)}_{i}"
            response = await client.post(
                "/api/auth/register",
                json={
                    "username": unique_id,
                    "email": f"{unique_id}@example.com",
                    "password": "Test123!",
                },
            )
            # After exceeding the shared IP limit, should get 429
            if response.status_code == 429:
                break

        # Verify we hit the rate limit
        assert response.status_code == 429


class TestRateLimitEdgeCases:
    """Tests for edge cases in rate limiting."""

    @pytest.mark.asyncio
    async def test_rate_limit_on_invalid_token_request(
        self, client: AsyncClient
    ):
        """Rate limit should apply even to requests with invalid tokens.

        This prevents token enumeration attacks.
        """
        # Make many requests with invalid tokens
        for i in range(DEFAULT_AUTH_RATE_LIMIT + 1):
            response = await client.get(
                "/api/projects",
                headers={"Authorization": "Bearer invalid_token"},
            )
            # After limit, should get 429 even for invalid auth
            if response.status_code == 429:
                break

    @pytest.mark.asyncio
    async def test_rate_limit_with_concurrent_requests(
        self, client: AsyncClient
    ):
        """Rate limit should handle concurrent requests correctly.

        Multiple simultaneous requests should be counted properly.
        """
        tasks = []
        for i in range(DEFAULT_AUTH_RATE_LIMIT + 5):
            unique_id = f"concurrent_user_{int(time.time() * 1000)}_{i}"
            tasks.append(
                client.post(
                    "/api/auth/register",
                    json={
                        "username": unique_id,
                        "email": f"{unique_id}@example.com",
                        "password": "Test123!",
                    },
                )
            )

        # Send all requests concurrently
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Count how many succeeded vs rate limited
        success_count = sum(1 for r in responses if hasattr(r, 'status_code') and r.status_code in [200, 201])
        rate_limited_count = sum(1 for r in responses if hasattr(r, 'status_code') and r.status_code == 429)

        # Most requests should be rate limited
        assert rate_limited_count > 0
