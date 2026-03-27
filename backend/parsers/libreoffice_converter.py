"""LibreOffice converter module for DOCX/DOC to HTML conversion."""

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LibreOfficeConversionError(Exception):
    """Raised when LibreOffice conversion fails."""

    pass


class LibreOfficeConverter:
    """LibreOffice converter for DOCX/DOC files to HTML format.

    Uses --convert-to html mode to preserve document structure including
    headings, tables, lists, and images in a separate directory.
    """

    def __init__(self, timeout: int = 900):
        """Initialize the converter.

        Args:
            timeout: Maximum time in seconds for conversion (default: 15 minutes)
        """
        self.timeout = timeout

    async def convert(self, file_path: Path, output_dir: Optional[Path] = None) -> dict:
        """Convert a DOCX/DOC file to HTML format.

        Args:
            file_path: Path to the input DOCX/DOC file
            output_dir: Optional output directory. If None, a temp directory is created.

        Returns:
            Dict containing:
                - html_path: Path to the generated HTML file
                - images_dir: Path to the directory containing extracted images
                - text: HTML content as string

        Raises:
            LibreOfficeConversionError: If conversion fails
            FileNotFoundError: If input file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix not in [".docx", ".doc"]:
            raise ValueError(f"Unsupported file type: {suffix}. Expected .docx or .doc")

        file_size = file_path.stat().st_size
        logger.info(f"LibreOffice HTML conversion: {file_path} ({file_size / (1024 * 1024):.2f}MB)")

        if output_dir is None:
            tmpdir = tempfile.mkdtemp(prefix="lo_convert_")
            output_dir = Path(tmpdir)
        else:
            output_dir.mkdir(parents=True, exist_ok=True)

        # Use thread pool to run subprocess since it's blocking
        html_path, images_dir = await asyncio.get_event_loop().run_in_executor(
            None, self._convert_sync, file_path, output_dir
        )

        # Read the generated HTML
        if html_path and html_path.exists():
            text = html_path.read_text(encoding="utf-8")
        else:
            raise LibreOfficeConversionError("LibreOffice did not generate HTML output")

        logger.info(f"LibreOffice conversion successful: {len(text)} characters")

        return {
            "html_path": html_path,
            "images_dir": images_dir,
            "text": text,
        }

    def _convert_sync(self, file_path: Path, output_dir: Path) -> tuple[Optional[Path], Optional[Path]]:
        """Synchronous conversion implementation.

        Args:
            file_path: Path to input file
            output_dir: Output directory

        Returns:
            Tuple of (html_path, images_dir)
        """
        abs_path = file_path.resolve()
        stem = file_path.stem

        # Run LibreOffice conversion to HTML
        # --headless: No GUI mode
        # --convert-to html: Convert to HTML format with image extraction
        # --outdir: Output directory
        result = subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "html",
                "--outdir",
                str(output_dir),
                str(abs_path),
            ],
            capture_output=True,
            timeout=self.timeout,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.error(f"LibreOffice conversion failed: {stderr}")
            raise LibreOfficeConversionError(f"LibreOffice conversion failed: {stderr}")

        # Find the generated HTML file
        # LibreOffice creates stem.html and a stem_files/ directory for images
        html_path = output_dir / f"{stem}.html"

        if not html_path.exists():
            # Try to find any HTML file in case LibreOffice used a different name
            html_files = list(output_dir.glob("*.html"))
            if html_files:
                html_path = html_files[0]
            else:
                stderr = result.stderr.decode() if result.stderr else ""
                raise LibreOfficeConversionError(f"LibreOffice did not generate HTML file: {stderr}")

        # Find the images directory (usually stem_files/)
        images_dir = output_dir / f"{stem}_files"
        if not images_dir.exists():
            # Try stem.files (Windows-style)
            images_dir_alt = output_dir / f"{stem}.files"
            if images_dir_alt.exists():
                images_dir = images_dir_alt
            else:
                # LibreOffice sometimes places images directly in output directory
                # with names like {stem}_html_{hash}.{ext}
                # Check if such images exist in the output directory itself
                image_patterns = [
                    f"{stem}_html_*.gif",
                    f"{stem}_html_*.png",
                    f"{stem}_html_*.jpg",
                    f"{stem}_html_*.jpeg",
                    f"{stem}_html_*.svg",
                    f"{stem}_html_*.bmp",
                    f"{stem}_files/*.gif",
                    f"{stem}_files/*.png",
                    f"{stem}_files/*.jpg",
                    f"{stem}_files/*.jpeg",
                    f"{stem}_files/*.svg",
                    f"{stem}_files/*.bmp",
                ]
                found_images = []
                for pattern in image_patterns:
                    found_images.extend(output_dir.glob(pattern))
                    if found_images:
                        break

                if found_images:
                    # Images are directly in output_dir, use output_dir as images_dir
                    images_dir = output_dir
                    logger.info(f"Images found directly in output directory: {len(found_images)} files")
                else:
                    images_dir = None
                    logger.warning(f"No images directory found for {stem}, checked patterns: {image_patterns}")

        return html_path, images_dir


# Module-level convenience function
async def convert_to_html(file_path: Path, output_dir: Optional[Path] = None) -> dict:
    """Convert a DOCX/DOC file to HTML format.

    Args:
        file_path: Path to the input DOCX/DOC file
        output_dir: Optional output directory

    Returns:
        Dict with html_path, images_dir, and text

    Raises:
        FileNotFoundError: If input file doesn't exist
        LibreOfficeConversionError: If conversion fails
        ValueError: If file type is not supported
    """
    converter = LibreOfficeConverter()
    return await converter.convert(file_path, output_dir)
