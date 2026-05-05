"""Tests for MarkitdownConverter and DirectFileImageHandler."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from backend.parsers.markitdown_converter import (
    MarkitdownConverter,
    MarkitdownConversionError,
    ConversionResult,
    ImageInfo,
    DirectFileImageHandler,
)


class TestMarkitdownConverter:

    def test_converter_initialization(self):
        converter = MarkitdownConverter()
        assert converter.timeout == 300

    def test_unsupported_file_type(self, tmp_path):
        unsupported_file = tmp_path / "document.pdf"
        unsupported_file.write_text("dummy content")
        converter = MarkitdownConverter()
        with pytest.raises(ValueError) as exc_info:
            converter.convert(unsupported_file)
        assert "Unsupported file type" in str(exc_info.value)

    def test_nonexistent_file(self):
        converter = MarkitdownConverter()
        with pytest.raises(FileNotFoundError):
            converter.convert(Path("/nonexistent/document.docx"))

    @pytest.mark.integration
    def test_convert_sample_docx(self, tmp_path):
        pytest.skip("Requires sample DOCX file")


class TestDirectFileImageHandler:
    """Test the DirectFileImageHandler that writes images directly to disk."""

    def test_handler_writes_image_to_disk(self, tmp_path):
        images_dir = tmp_path / "doc_images"
        handler = DirectFileImageHandler(images_dir, "doc_images")

        mock_image = MagicMock()
        mock_image.content_type = "image/png"
        mock_image.open.return_value.__enter__ = lambda s: MagicMock(read=lambda: b"\x89PNG\r\n\x1a\n")
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)

        result = handler(mock_image)

        assert result == {"src": "doc_images/image_1.png"}
        assert (images_dir / "image_1.png").exists()
        assert (images_dir / "image_1.png").read_bytes() == b"\x89PNG\r\n\x1a\n"
        assert len(handler.images) == 1
        assert handler.images[0].filename == "image_1.png"
        assert handler.images[0].data == b"\x89PNG\r\n\x1a\n"

    def test_handler_sequential_naming(self, tmp_path):
        images_dir = tmp_path / "doc_images"
        handler = DirectFileImageHandler(images_dir, "doc_images")

        for i in range(3):
            mock_image = MagicMock()
            mock_image.content_type = "image/jpeg"
            mock_image.open.return_value.__enter__ = lambda s: MagicMock(read=lambda: f"img{i}".encode())
            mock_image.open.return_value.__exit__ = MagicMock(return_value=False)
            handler(mock_image)

        assert len(handler.images) == 3
        assert handler.images[0].filename == "image_1.jpeg"
        assert handler.images[1].filename == "image_2.jpeg"
        assert handler.images[2].filename == "image_3.jpeg"
        assert (images_dir / "image_1.jpeg").exists()
        assert (images_dir / "image_2.jpeg").exists()
        assert (images_dir / "image_3.jpeg").exists()

    def test_handler_creates_directory(self, tmp_path):
        images_dir = tmp_path / "nested" / "images"
        handler = DirectFileImageHandler(images_dir, "nested/images")

        mock_image = MagicMock()
        mock_image.content_type = "image/png"
        mock_image.open.return_value.__enter__ = lambda s: MagicMock(read=lambda: b"data")
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)

        handler(mock_image)

        assert images_dir.exists()
        assert (images_dir / "image_1.png").exists()

    def test_handler_default_ext_for_unknown_content_type(self, tmp_path):
        images_dir = tmp_path / "doc_images"
        handler = DirectFileImageHandler(images_dir, "doc_images")

        mock_image = MagicMock()
        mock_image.content_type = None
        mock_image.open.return_value.__enter__ = lambda s: MagicMock(read=lambda: b"data")
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)

        result = handler(mock_image)

        assert result == {"src": "doc_images/image_1.png"}
        assert handler.images[0].filename == "image_1.png"

    def test_handler_returns_file_path_reference(self, tmp_path):
        """Verify handler returns a lightweight file path, NOT a data URI."""
        images_dir = tmp_path / "my_doc_images"
        handler = DirectFileImageHandler(images_dir, "my_doc_images")

        mock_image = MagicMock()
        mock_image.content_type = "image/png"
        mock_image.open.return_value.__enter__ = lambda s: MagicMock(read=lambda: b"x" * 1000)
        mock_image.open.return_value.__exit__ = MagicMock(return_value=False)

        result = handler(mock_image)

        assert "data:" not in result["src"]
        assert "base64" not in result["src"]
        assert result["src"] == "my_doc_images/image_1.png"
