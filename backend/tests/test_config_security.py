"""Tests for configuration security - no hardcoded credentials in source code."""

import inspect
from backend.config import Settings


class TestConfigSecurity:
    """Verify configuration source code has no hardcoded credentials.

    The .env file (gitignored) contains development credentials for local testing.
    This test verifies the SOURCE CODE (config.py) has no hardcoded production
    credentials as default values.
    """

    def test_database_url_default_is_empty_or_placeholder(self):
        """Database URL default in source code should be empty or placeholder, not real credentials."""
        # Get the field default from the class, not from Settings instance
        field_default = Settings.model_fields["database_url"].default

        # Should be empty string or placeholder, not a real URL with credentials
        assert field_default == "" or "your-" in field_default, \
            f"Database URL source code default contains real credentials: {field_default}"

    def test_redis_url_default_is_empty_or_placeholder(self):
        """Redis URL default in source code should be empty or placeholder."""
        field_default = Settings.model_fields["redis_url"].default

        assert field_default == "" or "your-" in field_default or "localhost" in field_default, \
            f"Redis URL source code default contains hardcoded IP: {field_default}"

    def test_celery_broker_url_default_is_empty(self):
        """Celery broker URL default in source code should be empty."""
        field_default = Settings.model_fields["celery_broker_url"].default

        assert field_default == "" or "localhost" in field_default, \
            f"Celery broker URL source code default contains hardcoded IP: {field_default}"

    def test_secret_key_default_is_empty_or_placeholder(self):
        """Secret key default in source code should be empty or placeholder."""
        field_default = Settings.model_fields["secret_key"].default

        assert field_default == "" or "your-" in field_default, \
            f"Secret key source code default is not placeholder: {field_default}"
