import base64
import ipaddress
import mimetypes
import socket
from pathlib import Path
from typing import Tuple
from urllib.parse import urljoin, urlparse

import requests

from .config import (
    DEFAULT_MIME_TYPE,
    IMAGE_DOWNLOAD_TIMEOUT,
    MAX_IMAGE_SIZE_BYTES,
    SUPPORTED_IMAGE_FORMATS,
)
from .exceptions import InvalidImageError, NetworkError
from .logger import logger

MAX_REDIRECTS = 3

# Magic bytes for image format detection
_MAGIC_BYTES = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'RIFF': 'image/webp',  # WebP starts with RIFF....WEBP
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
}


class ImageProcessor:
    @staticmethod
    def is_url(path: str) -> bool:
        parsed = urlparse(path)
        return parsed.scheme in ("http", "https")

    @staticmethod
    def validate_image_format(path: str) -> None:
        if ImageProcessor.is_url(path):
            return

        extension = Path(path).suffix.lstrip(".").lower()
        if extension and extension not in SUPPORTED_IMAGE_FORMATS:
            raise InvalidImageError(
                f"Unsupported image format: {extension}. "
                f"Supported formats: {', '.join(SUPPORTED_IMAGE_FORMATS)}"
            )

    @staticmethod
    def detect_mime_type(image_data: bytes, filename: str = "") -> str:
        """Detect MIME type using magic bytes first, then filename fallback."""
        # Check magic bytes (most reliable)
        for magic, mime in _MAGIC_BYTES.items():
            if image_data[:len(magic)] == magic:
                # Extra check for WebP: RIFF header must also contain WEBP
                if magic == b'RIFF' and image_data[8:12] != b'WEBP':
                    continue
                logger.debug(f"Detected MIME type from magic bytes: {mime}")
                return mime

        # Fallback to filename extension
        if filename:
            guessed, _ = mimetypes.guess_type(filename)
            if guessed and guessed.startswith("image/"):
                logger.debug(f"Detected MIME type from filename: {guessed}")
                return guessed

        logger.debug(f"Could not detect MIME type, defaulting to {DEFAULT_MIME_TYPE}")
        return DEFAULT_MIME_TYPE

    @staticmethod
    def _validate_size(data: bytes, source: str) -> None:
        """Enforce image size limit."""
        if len(data) > MAX_IMAGE_SIZE_BYTES:
            size_mb = len(data) / (1024 * 1024)
            limit_mb = MAX_IMAGE_SIZE_BYTES / (1024 * 1024)
            raise InvalidImageError(
                f"Image too large ({size_mb:.1f} MB). Maximum size is {limit_mb:.0f} MB."
            )

    @staticmethod
    def _assert_public_host(url: str) -> None:
        """Block requests to loopback/private/link-local addresses (SSRF guard)."""
        host = urlparse(url).hostname
        if not host:
            raise InvalidImageError(f"Invalid image URL: {url}")
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            raise NetworkError(f"Could not resolve host: {host}")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise InvalidImageError(
                    f"Refusing to fetch image from non-public address: {host}"
                )

    @staticmethod
    def download_image(url: str) -> bytes:
        try:
            logger.info(f"Downloading image from URL: {url}")
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                ImageProcessor._assert_public_host(current)
                response = requests.get(
                    current, timeout=IMAGE_DOWNLOAD_TIMEOUT,
                    stream=True, allow_redirects=False,
                )
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("Location", ""))
                    response.close()
                    if not ImageProcessor.is_url(current):
                        raise InvalidImageError("Redirected to a non-HTTP URL")
                    continue
                break
            else:
                raise NetworkError("Too many redirects")

            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image/"):
                raise InvalidImageError(
                    f"URL did not return an image. Content-Type: {content_type}"
                )

            # Read in chunks so an oversized file is rejected before it is fully downloaded
            chunks, total = [], 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_IMAGE_SIZE_BYTES:
                    response.close()
                    raise InvalidImageError(
                        f"Image too large. Maximum size is "
                        f"{MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB."
                    )
                chunks.append(chunk)
            data = b"".join(chunks)

            logger.debug(f"Successfully downloaded {len(data)} bytes")
            return data

        except (InvalidImageError, NetworkError):
            raise
        except requests.exceptions.Timeout:
            raise NetworkError(f"Request timed out when downloading from: {url}")
        except requests.exceptions.ConnectionError:
            raise NetworkError(f"Connection failed for URL: {url}")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            raise NetworkError(f"HTTP {status} error downloading image from: {url}")
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Failed to download image: {e}")

    @staticmethod
    def load_local_image(path: str) -> bytes:
        try:
            logger.info(f"Loading local image: {path}")
            with open(path, "rb") as file:
                content = file.read()

            # Validate size
            ImageProcessor._validate_size(content, path)

            logger.debug(f"Successfully loaded {len(content)} bytes")
            return content

        except InvalidImageError:
            raise
        except FileNotFoundError:
            raise InvalidImageError(f"Image file not found: {path}")
        except PermissionError:
            raise InvalidImageError(f"Permission denied accessing: {path}")
        except Exception as e:
            raise InvalidImageError(f"Failed to read image file: {e}")

    @staticmethod
    def encode_to_base64(image_data: bytes) -> str:
        return base64.b64encode(image_data).decode("utf-8")

    @classmethod
    def process_image(cls, image_path: str) -> Tuple[str, str]:
        """Process image and return (base64_data, mime_type) tuple."""
        cls.validate_image_format(image_path)

        if cls.is_url(image_path):
            image_data = cls.download_image(image_path)
        else:
            image_data = cls.load_local_image(image_path)

        mime_type = cls.detect_mime_type(image_data, image_path)
        base64_data = cls.encode_to_base64(image_data)

        return base64_data, mime_type
