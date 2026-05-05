"""Tests for document_parser module, specifically _parse_docx function."""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.parsers.markitdown_converter import ImageInfo


class TestParseDocx:

    def test_parse_docx_function_exists(self):
        from backend.tasks.document_parser import _parse_docx
        assert callable(_parse_docx)

    def test_parse_docx_returns_dict_with_text_and_images(self, tmp_path):
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        mock_result = MagicMock()
        mock_result.markdown_content = "Test content"
        mock_result.images = []

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        assert isinstance(result, dict)
        assert "text" in result
        assert "images" in result
        assert "page_count" in result
        assert result["page_count"] is None

    def test_parse_docx_passes_images_dir_to_converter(self, tmp_path):
        """Verify _parse_docx passes the correct images_dir to converter."""
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "my_doc.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        mock_result = MagicMock()
        mock_result.markdown_content = "Content"
        mock_result.images = []

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        # Verify converter was called with correct images_dir
        mock_converter.convert.assert_called_once()
        call_kwargs = mock_converter.convert.call_args
        assert call_kwargs.kwargs.get("images_dir") == tmp_path / "my_doc_images"

    def test_parse_docx_markdown_already_has_file_paths(self, tmp_path):
        """With DirectFileImageHandler, markdown contains file paths, not base64."""
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "test_image.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        # Simulate what DirectFileImageHandler produces: file-path references, no base64
        mock_result = MagicMock()
        mock_result.markdown_content = (
            "Document with image ![image_1](test_image_images/image_1.png) end"
        )
        mock_result.images = [
            ImageInfo(filename="image_1.png", data=b"\x89PNG\r\n\x1a\n")
        ]

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        markdown_output = result["text"]

        # Markdown should have file path references
        assert "test_image_images/image_1.png" in markdown_output
        # No base64 data URIs should be present
        assert "data:image" not in markdown_output
        assert "base64" not in markdown_output


class TestParseDocxEdgeCases:

    def test_parse_docx_with_no_images(self, tmp_path):
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "text_only.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        mock_result = MagicMock()
        mock_result.markdown_content = "Plain text document without images"
        mock_result.images = []

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        assert result["text"] == "Plain text document without images"
        assert result["images"] == []

    def test_parse_docx_with_many_images(self, tmp_path):
        """Verify handling of documents with multiple images."""
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "multi_image.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        images = [
            ImageInfo(filename=f"image_{i}.png", data=f"img{i}".encode())
            for i in range(1, 6)
        ]
        markdown_parts = [
            f"![image_{i}](multi_image_images/image_{i}.png)"
            for i in range(1, 6)
        ]

        mock_result = MagicMock()
        mock_result.markdown_content = "Between " + " and ".join(markdown_parts) + " end"
        mock_result.images = images

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        assert len(result["images"]) == 5
        for i in range(1, 6):
            assert f"image_{i}.png" in result["text"]
        assert "data:image" not in result["text"]
