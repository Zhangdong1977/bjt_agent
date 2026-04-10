"""Tests for MarkitdownConverter."""

import pytest
from pathlib import Path
from backend.parsers.markitdown_converter import (
    MarkitdownConverter,
    MarkitdownConversionError,
    ConversionResult,
    ImageInfo,
)


class TestMarkitdownConverter:
    """Test cases for MarkitdownConverter."""

    def test_converter_initialization(self):
        """Test converter can be initialized."""
        converter = MarkitdownConverter()
        assert converter.timeout == 300

    def test_unsupported_file_type(self, tmp_path):
        """Test that unsupported file types raise ValueError."""
        # Create a real file with unsupported extension
        unsupported_file = tmp_path / "document.pdf"
        unsupported_file.write_text("dummy content")
        converter = MarkitdownConverter()
        with pytest.raises(ValueError) as exc_info:
            converter.convert(unsupported_file)
        assert "Unsupported file type" in str(exc_info.value)

    def test_nonexistent_file(self):
        """Test that nonexistent files raise FileNotFoundError."""
        converter = MarkitdownConverter()
        with pytest.raises(FileNotFoundError):
            converter.convert(Path("/nonexistent/document.docx"))

    @pytest.mark.integration
    def test_convert_sample_docx(self, tmp_path):
        """Integration test: convert a sample DOCX file."""
        # This test requires a real DOCX file
        # Skip if no sample file available
        pytest.skip("Requires sample DOCX file")