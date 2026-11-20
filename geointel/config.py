from typing import Final

# API Configuration
GEMINI_API_BASE_URL: Final[str] = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL: Final[str] = "gemini-3.8-flash"  # Default model
API_TIMEOUT: Final[int] = 240  # Reasoning models can take minutes under load

# Available models (id -> display name)
AVAILABLE_MODELS: Final[dict] = {
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro (Most Accurate)",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
}

# Generation Configuration
DEFAULT_TEMPERATURE: Final[float] = 0.3  # Lower for more consistent/accurate location predictions
DEFAULT_TOP_K: Final[int] = 40
DEFAULT_TOP_P: Final[float] = 0.95
MAX_OUTPUT_TOKENS: Final[int] = 16384

# Image Processing
SUPPORTED_IMAGE_FORMATS: Final[tuple] = ("jpeg", "jpg", "png", "webp", "gif")
DEFAULT_MIME_TYPE: Final[str] = "image/jpeg"
IMAGE_DOWNLOAD_TIMEOUT: Final[int] = 10

# Response Configuration
MAX_LOCATIONS: Final[int] = 3
CONFIDENCE_LEVELS: Final[tuple] = ("High", "Medium", "Low")

# Image Size Limits
MAX_IMAGE_SIZE_BYTES: Final[int] = 20 * 1024 * 1024  # 20 MB

# Environment Variables
ENV_API_KEY: Final[str] = "GEMINI_API_KEY"
