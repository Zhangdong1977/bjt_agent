"""Tests for SubAgentExecutor."""

import pytest
from backend.agent.master.sub_agent_executor import detect_anomaly


def test_detect_anomaly_empty_result():
    """Test anomaly detection with empty result."""
    assert detect_anomaly({}, None) is True
    assert detect_anomaly({"success": True, "findings": []}, None) is True


def test_detect_anomaly_failed_result():
    """Test anomaly detection with failed result."""
    result = {"success": False, "error": "Some error occurred"}
    assert detect_anomaly(result, None) is True


def test_detect_anomaly_all_compliant_but_missing():
    """Test anomaly detection when all findings are compliant but some are missing."""
    todo = type("TodoItem", (), {"check_items": [{"id": 1}, {"id": 2}, {"id": 3}]})()
    result = {
        "success": True,
        "findings": [
            {"is_compliant": True, "requirement_key": "1"},
            {"is_compliant": True, "requirement_key": "2"},
        ],
    }
    assert detect_anomaly(result, todo) is True


def test_detect_anomaly_all_compliant_complete():
    """Test no anomaly when all findings are compliant and complete."""
    todo = type("TodoItem", (), {"check_items": [{"id": 1}, {"id": 2}, {"id": 3}]})()
    result = {
        "success": True,
        "findings": [
            {"is_compliant": True, "requirement_key": "1"},
            {"is_compliant": True, "requirement_key": "2"},
            {"is_compliant": True, "requirement_key": "3"},
        ],
    }
    assert detect_anomaly(result, todo) is False


def test_detect_anomaly_has_non_compliant():
    """Test no anomaly when there are non-compliant findings."""
    todo = type("TodoItem", (), {"check_items": [{"id": 1}, {"id": 2}]})()
    result = {
        "success": True,
        "findings": [
            {"is_compliant": True, "requirement_key": "1"},
            {"is_compliant": False, "requirement_key": "2"},
        ],
    }
    assert detect_anomaly(result, todo) is False