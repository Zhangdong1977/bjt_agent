"""Unit tests for LibreOffice converter module."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestLibreOfficeConverter:
    """Tests for LibreOfficeConverter class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def sample_docx_path(self, temp_dir):
        """Create a sample DOCX file path (doesn't actually create the file)."""
        doc_path = temp_dir / "sample.docx"
        # Create an empty file just to test path handling
        doc_path.touch()
        return doc_path

    def test_conversion_error_not_exists(self):
        """Test that FileNotFoundError is raised for non-existent files."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter

        converter = LibreOfficeConverter()
        with pytest.raises(FileNotFoundError):
            asyncio.run(converter.convert(Path("/nonexistent/file.docx")))

    def test_conversion_error_unsupported_type(self, temp_dir):
        """Test that ValueError is raised for unsupported file types."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter

        converter = LibreOfficeConverter()
        txt_path = temp_dir / "sample.txt"
        txt_path.touch()

        with pytest.raises(ValueError, match="Unsupported file type"):
            asyncio.run(converter.convert(txt_path))

    def test_conversion_error_libreoffice_failed(self, sample_docx_path):
        """Test that LibreOfficeConversionError is raised when conversion fails."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter, LibreOfficeConversionError

        converter = LibreOfficeConverter()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr=b"Conversion failed"
            )

            with pytest.raises(LibreOfficeConversionError, match="Conversion failed"):
                asyncio.run(converter.convert(sample_docx_path))

    def test_conversion_error_no_html_output(self, sample_docx_path):
        """Test error when LibreOffice doesn't generate HTML output."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter, LibreOfficeConversionError

        converter = LibreOfficeConverter()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stderr=b""
            )
            # Mock the output directory to have no HTML files
            with patch("pathlib.Path.glob", return_value=[]):
                with pytest.raises(LibreOfficeConversionError, match="did not generate HTML file"):
                    asyncio.run(converter.convert(sample_docx_path))

    def test_conversion_success_with_html(self, sample_docx_path, temp_dir):
        """Test successful conversion to HTML."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter

        converter = LibreOfficeConverter()

        html_content = "<html><body><h1>Test</h1></body></html>"
        html_path = temp_dir / "sample.html"
        html_path.write_text(html_content)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr=b"")

            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value=html_content):
                    with patch("pathlib.Path.glob", return_value=[html_path]):
                        result = asyncio.run(converter.convert(sample_docx_path, temp_dir))

        assert "text" in result
        assert "html_path" in result
        assert "images_dir" in result

    def test_convert_to_html_function(self):
        """Test the module-level convert_to_html function."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter, convert_to_html

        # Create a real temp file to avoid FileNotFoundError
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = Path(tmpdir) / "test.docx"
            docx_path.write_bytes(b"fake docx content")
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()  # Create the output directory first

            html_content = "<html><body><p>Test</p></body></html>"
            html_path = output_dir / "test.html"
            html_path.write_text(html_content)

            # Mock the subprocess.run to avoid actual LibreOffice call
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr=b"")

                # Also mock Path.glob to find our HTML file
                with patch.object(Path, "glob", return_value=[html_path]):
                    result = asyncio.run(convert_to_html(docx_path, output_dir))

        assert "text" in result
        assert result["text"] == html_content
        assert result["html_path"] == html_path

    def test_timeout_parameter(self):
        """Test that timeout parameter is respected."""
        from backend.parsers.libreoffice_converter import LibreOfficeConverter

        converter = LibreOfficeConverter(timeout=300)
        assert converter.timeout == 300

        converter2 = LibreOfficeConverter()
        assert converter2.timeout == 900  # default


class TestLibreOfficeConversionError:
    """Tests for LibreOfficeConversionError exception."""

    def test_error_message(self):
        """Test that error message is properly set."""
        from backend.parsers.libreoffice_converter import LibreOfficeConversionError

        error = LibreOfficeConversionError("Test error message")
        assert str(error) == "Test error message"

    def test_error_is_exception(self):
        """Test that error inherits from Exception."""
        from backend.parsers.libreoffice_converter import LibreOfficeConversionError

        error = LibreOfficeConversionError("Test")
        assert isinstance(error, Exception)
