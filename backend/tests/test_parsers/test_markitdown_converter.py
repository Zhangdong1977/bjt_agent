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


class TestPlaceholderReplacement:
    """Test cases for image placeholder replacement in markdown."""

    def test_placeholder_pattern_matches_full_base64_format(self):
        """Test that placeholder regex matches full base64 data URI format.

        markitdown with keep_data_uris=True outputs:
        ![](data:image/jpeg;base64,/9j/4AAQSkZJRg...)

        Not the truncated format:
        ![](data:image/jpeg;base64...)
        """
        import re

        # Current pattern (only matches truncated format)
        truncated_pattern = r'!\[\]\(data:image/([^;]+);base64\.\.\.\)'
        # Fixed pattern (matches full base64 format)
        full_pattern = r'!\[\]\(data:image/([^;]+);base64,([^)]+)\)'

        # Full base64 markdown content from markitdown with keep_data_uris=True
        markdown_with_full_base64 = (
            "Some text before ![](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD"
            "2wBBDAAibaRsZG1lZCBJQyACg4NHCB8V2hpbiB0cmF2ZXIgMjAyMaoUExBZGdjY2dzbnz/"
            "wAARCADE AlgoQDKAD/2Q==) some text after"
        )

        # Truncated format (what old code expected)
        markdown_with_truncated = "Some text ![](data:image/png;base64...) more text"

        # Verify truncated pattern works on truncated format
        assert re.findall(truncated_pattern, markdown_with_truncated)

        # Verify full pattern works on full format
        full_matches = re.findall(full_pattern, markdown_with_full_base64)
        assert len(full_matches) == 1
        assert full_matches[0] == ('jpeg', '/9j/4AAQSkZJRgABAQEASABIAAD2wBBDAAibaRsZG1lZCBJQyACg4NHCB8V2hpbiB0cmF2ZXIgMjAyMaoUExBZGdjY2dzbnz/wAARCADE AlgoQDKAD/2Q==')

        # This is the BUG: current pattern does NOT match full base64 format
        truncated_matches = re.findall(truncated_pattern, markdown_with_full_base64)
        assert len(truncated_matches) == 0, "BUG: current pattern should not match full base64"

    def test_placeholder_replacement_uses_file_path_not_base64(self, tmp_path):
        """Test that placeholder replacement uses file path, not base64 data.

        When markitdown outputs full base64 data URIs, the replacement should
        convert them to file references like ![](image_name/image.png),
        NOT keep the base64 data.
        """
        import re
        import base64

        # Create a test image file
        images_dir = tmp_path / "test_doc_images"
        images_dir.mkdir()
        test_img = images_dir / "test_image.png"
        test_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

        images_dir_name = "test_doc_images"

        # Markdown content with full base64 placeholders from markitdown
        markdown_content = (
            "Text before ![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEA"
            "AAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==) text after"
        )

        # Current buggy pattern (only matches truncated format)
        placeholder_pattern = r'!\[\]\(data:image/([^;]+);base64\.\.\.\)'

        # Fixed pattern (matches full base64 format)
        fixed_pattern = r'!\[\]\(data:image/([^;]+);base64,([^)]+)\)'

        # Find placeholders with fixed pattern
        placeholders = re.findall(fixed_pattern, markdown_content)
        assert len(placeholders) == 1, "Should find one placeholder with full base64 format"

        # Get image files
        image_files = list(images_dir.glob('*'))
        assert len(image_files) == 1

        # Build replacement using file path (NOT base64)
        img_file = image_files[0]
        expected_replacement = f"![{img_file.stem}]({images_dir_name}/{img_file.name})"

        # Perform replacement
        new_content = re.sub(fixed_pattern, expected_replacement, markdown_content, count=1)

        # Verify replacement does NOT contain base64 data
        assert "base64" not in new_content.lower(), "Replacement should not contain base64"
        assert expected_replacement in new_content, "Replacement should use file path"
        assert "iVBORw0KGgo" not in new_content, "Base64 data should be removed"


class TestDocumentParserPlaceholderBug:
    """Test that exposes the placeholder replacement bug in document_parser.py.

    BUG: document_parser.py line 475 uses pattern that only matches truncated format
    (data:image/jpeg;base64...) but markitdown outputs full base64 data.
    """

    def test_bug_placeholder_not_replaced_with_file_path(self, tmp_path, monkeypatch):
        """Test demonstrating the bug: placeholders not replaced when markitdown outputs full base64.

        This test should FAIL with current buggy code, then PASS after fix.
        """
        import re
        import base64
        from unittest.mock import MagicMock, patch

        from backend.tasks.document_parser import _parse_docx

        # Create a mock DOCX file path
        docx_path = tmp_path / "test_document.docx"
        docx_path.write_bytes(b"PK\x03\x04")  # Minimal DOCX-like bytes

        # Create images directory with a real image file
        images_dir = tmp_path / "test_document_images"
        images_dir.mkdir()
        test_img = images_dir / "embedded_image.png"
        # Write minimal PNG bytes
        test_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00:\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")

        # Create a mock ConversionResult with FULL base64 data URI (not truncated)
        # This is what markitdown actually outputs with keep_data_uris=True
        mock_result = MagicMock()
        mock_result.markdown_content = (
            "Document with image ![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA"
            "EAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==) end"
        )
        mock_result.images = [
            MagicMock(filename="embedded_image.png", data=test_img.read_bytes())
        ]

        # Patch MarkitdownConverter - it's imported from backend.parsers.markitdown_converter
        with patch('backend.parsers.markitdown_converter.MarkitdownConverter') as mock_converter_class:
            mock_converter = MagicMock()
            mock_converter.convert.return_value = mock_result
            mock_converter_class.return_value = mock_converter

            # Run the actual parsing
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(_parse_docx(docx_path))

        markdown_output = result["text"]

        # BUG VERIFICATION: The markdown should contain file reference, NOT base64 data
        # With buggy code: placeholder is NOT matched (pattern expects truncated format)
        # so output still contains the full base64 data URI
        #
        # After fix: output should contain ![embedded_image](test_document_images/embedded_image.png)

        # Check that base64 data is NOT in output (it should be replaced with file path)
        has_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==" in markdown_output

        # The bug is: with truncated pattern, no replacement happens, base64 stays in output
        if has_base64:
            pytest.fail(
                "BUG CONFIRMED: Placeholder was not replaced with file path. "
                "The base64 data is still in the markdown output. "
                "This is because the regex pattern only matches truncated format (base64...) "
                "but markitdown outputs full base64 data (base64,<data>)."
            )

        # After fix, this should pass
        assert "embedded_image" in markdown_output
        assert "test_document_images/embedded_image.png" in markdown_output