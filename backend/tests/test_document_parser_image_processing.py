"""Tests for document parser image processing functionality."""
import sys
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# Mock the backend.models module before importing document_parser
# This avoids database connection issues during test collection
mock_models = MagicMock()
sys.modules['backend.models'] = mock_models
mock_models.async_session_factory = None
mock_models.Document = MagicMock()

mock_celery_app = MagicMock()
sys.modules['backend.celery_app'] = mock_celery_app


@pytest.mark.asyncio
async def test_process_images_strips_think_tags():
    """Test _process_images_with_llm correctly strips AI think tags."""
    from backend.tasks.document_parser import _process_images_with_llm

    # Create mock image data
    mock_images = [
        {
            "filename": "test_image.png",
            "data": b"fake_image_data"
        }
    ]

    # Mock API response containing think-tag
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": "这是一个图片描述。<thought>AI思考过程不应该显示</thought>正文继续。"
            }
        }]
    }

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images,
            "fake_api_key",
            "https://api.minimaxi.com",
            "MiniMax-M2.7-highspeed"
        )

    # Verify result does not contain think-tag
    assert len(result) == 1
    assert "<think>" not in result[0]
    assert "</think>" not in result[0]
    assert "AI思考过程不应该显示" not in result[0]
    # Verify normal content is preserved
    assert "这是一个图片描述。" in result[0]
    assert "正文继续。" in result[0]


@pytest.mark.asyncio
async def test_process_images_api_error_returns_empty_description():
    """Test API error is handled - no description added for failed requests."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value.status_code = 500

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    # When API returns error, no description is added (skipped with warning)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_process_images_handles_json_decode_error():
    """Test JSON decode error is handled - no description added on parse failure."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    with patch('httpx.AsyncClient') as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    # When JSON parsing fails, no description is added (skipped with warning)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_process_images_strips_multiple_think_tags():
    """Test multiple think tags are all stripped."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": "开始<think>思考1</think>中间<think>思考2</think>结束"
            }
        }]
    }

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    assert len(result) == 1
    assert "<think>" not in result[0]
    assert "</think>" not in result[0]
    assert "思考1" not in result[0]
    assert "思考2" not in result[0]
    assert "开始" in result[0]
    assert "中间" in result[0]
    assert "结束" in result[0]


@pytest.mark.asyncio
async def test_process_images_handles_empty_content():
    """Test empty content results in empty description appended."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": ""
            }
        }]
    }

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    # Empty content results in "[Image: test.png] " (empty description after strip)
    assert len(result) == 1
    assert result[0] == "[Image: test.png] "


@pytest.mark.asyncio
async def test_process_images_filename_prefix():
    """Test that image filename is correctly prefixed in result."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "page_1_img_1.png", "data": b"data"}]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "content": "A simple description"
            }
        }]
    }

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    assert len(result) == 1
    assert "[Image: page_1_img_1.png]" in result[0]
    assert "A simple description" in result[0]


@pytest.mark.asyncio
async def test_process_images_multiple_images():
    """Test processing multiple images."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [
        {"filename": "image1.png", "data": b"data1"},
        {"filename": "image2.jpg", "data": b"data2"},
    ]

    # Return different descriptions for each call
    mock_response1 = MagicMock()
    mock_response1.status_code = 200
    mock_response1.json.return_value = {
        "choices": [{"message": {"content": "Description 1"}}]
    }
    mock_response2 = MagicMock()
    mock_response2.status_code = 200
    mock_response2.json.return_value = {
        "choices": [{"message": {"content": "Description 2"}}]
    }

    with patch('httpx.AsyncClient') as mock_client:
        mock_post = mock_client.return_value.__aenter__.return_value.post
        mock_post.side_effect = [mock_response1, mock_response2]

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    assert len(result) == 2
    assert "[Image: image1.png]" in result[0]
    assert "Description 1" in result[0]
    assert "[Image: image2.jpg]" in result[1]
    assert "Description 2" in result[1]


@pytest.mark.asyncio
async def test_process_images_handles_empty_choices():
    """Test empty choices array is handled - no description added."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    with patch('httpx.AsyncClient') as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    # Should return empty result when choices is empty
    assert len(result) == 0


@pytest.mark.asyncio
async def test_process_images_handles_network_error():
    """Test network error is handled - no description added."""
    from backend.tasks.document_parser import _process_images_with_llm

    mock_images = [{"filename": "test.png", "data": b"data"}]

    with patch('httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post.side_effect = ConnectionError("Network failed")

        result = await _process_images_with_llm(
            mock_images, "key", "https://api.minimaxi.com", "model"
        )

    assert len(result) == 1
    assert "Image processing failed" in result[0]
