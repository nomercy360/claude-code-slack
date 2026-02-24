"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_user_id():
    """Sample Slack user ID for testing."""
    return "U01ABC123"


@pytest.fixture
def sample_config():
    """Sample configuration for testing."""
    return {
        "slack_bot_token": "xoxb-test-token",
        "slack_app_token": "xapp-test-token",
        "slack_signing_secret": "test_signing_secret",
        "approved_directory": "/tmp/test_projects",
        "allowed_users": ["U01ABC123"],
    }
