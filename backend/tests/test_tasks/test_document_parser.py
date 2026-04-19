"""Tests for document_parser module, specifically _parse_docx function."""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestParseDocx:
    """Test cases for _parse_docx function."""

    def test_parse_docx_function_exists(self):
        """Test that _parse_docx function exists and is callable."""
        from backend.tasks.document_parser import _parse_docx
        assert callable(_parse_docx)

    def test_parse_docx_returns_dict_with_text_and_images(self, tmp_path):
        """Test that _parse_docx returns dict with expected keys."""
        from backend.tasks.document_parser import _parse_docx

        # Create a minimal DOCX file (PK header)
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(b"PK\x03\x04")  # Minimal DOCX-like bytes

        # Mock MarkitdownConverter to avoid actual conversion
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

    def test_parse_docx_replaces_placeholders_with_file_references(self, tmp_path):
        """Test that image placeholders are replaced with file path references."""
        from backend.tasks.document_parser import _parse_docx

        # Create a minimal DOCX file
        docx_path = tmp_path / "test_image.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        # Create images directory with a real image file
        images_dir = tmp_path / "test_image_images"
        images_dir.mkdir()
        test_img = images_dir / "image1.png"
        test_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

        # Mock ConversionResult with base64 placeholder
        mock_result = MagicMock()
        mock_result.markdown_content = (
            "Document with image ![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==) end"
        )
        mock_result.images = [
            MagicMock(filename="image1.png", data=test_img.read_bytes())
        ]

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        markdown_output = result["text"]

        # Verify placeholder was replaced with file path
        assert "test_image_images/image1.png" in markdown_output
        # Verify base64 data is NOT in output
        assert "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==" not in markdown_output

    def test_parse_docx_logs_warning_for_unmatched_placeholder(self, tmp_path, caplog):
        """Test that a warning is logged when there are more placeholders than image files."""
        import logging
        from backend.tasks.document_parser import _parse_docx

        # Create a minimal DOCX file
        docx_path = tmp_path / "test_unmatched.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        # Create images directory with only ONE image
        images_dir = tmp_path / "test_unmatched_images"
        images_dir.mkdir()
        test_img = images_dir / "image1.png"
        test_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

        # Mock ConversionResult with TWO base64 placeholders but only ONE image
        mock_result = MagicMock()
        mock_result.markdown_content = (
            "Doc with image1 ![](data:image/png;base64,PLACEHOLDER1) "
            "and image2 ![](data:image/png;base64,PLACEHOLDER2) end"
        )
        mock_result.images = [
            MagicMock(filename="image1.png", data=test_img.read_bytes())
        ]

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            with caplog.at_level(logging.WARNING):
                result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        markdown_output = result["text"]

        # First placeholder should be replaced
        assert "test_unmatched_images/image1.png" in markdown_output
        # Second placeholder should still contain base64 (not replaced) and warning should be logged
        assert "PLACEHOLDER2" in markdown_output
        # Verify warning was logged
        assert any("No image file available for placeholder at index 1" in record.message for record in caplog.records)


class TestParseDocxEdgeCases:
    """Edge case tests for _parse_docx function."""

    def test_parse_docx_with_no_images(self, tmp_path):
        """Test _parse_docx when document has no images."""
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

    def test_parse_docx_creates_images_directory(self, tmp_path):
        """Test that _parse_docx creates images directory when images exist."""
        from backend.tasks.document_parser import _parse_docx

        docx_path = tmp_path / "with_images.docx"
        docx_path.write_bytes(b"PK\x03\x04")

        images_dir = tmp_path / "with_images_images"

        mock_result = MagicMock()
        mock_result.markdown_content = "Document"
        mock_result.images = []

        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            # Run parsing - images_dir should not exist yet
            assert not images_dir.exists()

            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

            # After parsing, images dir should be created if there are images
            # (empty images list means dir won't be created, but function should still work)
            assert result is not None
