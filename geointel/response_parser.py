import json
import re
from typing import Any, Dict, List, Optional

from .config import CONFIDENCE_LEVELS
from .exceptions import ResponseParsingError
from .logger import logger


def _to_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0  # NaN -> 0.0


class ResponseParser:

    @staticmethod
    def clean_json_string(text: str) -> str:
        """Strip markdown fences and any prose around the outermost JSON object."""
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        if start == -1:
            return text
        end = text.rfind("}")
        return text[start:end + 1] if end > start else text[start:]

    @staticmethod
    def validate_location(location: Any) -> bool:
        if not isinstance(location, dict):
            return False
        return all(field in location for field in ("country", "city", "confidence"))

    @staticmethod
    def normalize_confidence(confidence: Any) -> str:
        if not isinstance(confidence, str):
            return "Medium"
        confidence = confidence.strip().capitalize()
        return confidence if confidence in CONFIDENCE_LEVELS else "Medium"

    @staticmethod
    def normalize_coordinates(coords: Any) -> Dict[str, float]:
        if not isinstance(coords, dict):
            return {"latitude": 0.0, "longitude": 0.0}
        lat = _to_float(coords.get("latitude"))
        lng = _to_float(coords.get("longitude"))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            logger.warning(f"Coordinates out of range ({lat}, {lng}); resetting to 0,0")
            return {"latitude": 0.0, "longitude": 0.0}
        return {"latitude": lat, "longitude": lng}

    @staticmethod
    def normalize_location(location: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "country": str(location.get("country") or "Unknown"),
            "state": str(location.get("state") or ""),
            "city": str(location.get("city") or "Unknown"),
            "confidence": ResponseParser.normalize_confidence(location.get("confidence")),
            "coordinates": ResponseParser.normalize_coordinates(location.get("coordinates")),
            "explanation": str(location.get("explanation") or ""),
        }

    @staticmethod
    def _attempt_json_repair(raw_response: str) -> Optional[Dict[str, Any]]:
        """Recover whatever complete location data exists in a truncated response."""
        json_string = ResponseParser.clean_json_string(raw_response)

        interp_match = re.search(r'"interpretation"\s*:\s*"((?:[^"\\]|\\.)*)"', json_string)
        interpretation = interp_match.group(1) if interp_match else "Analysis was truncated"

        locations: List[Dict[str, Any]] = []
        pattern = (
            r'"country"\s*:\s*"([^"]*)"[^{}]*?"city"\s*:\s*"([^"]*)"'
            r'[^{}]*?"confidence"\s*:\s*"([^"]*)"'
        )
        for country, city, confidence in re.findall(pattern, json_string):
            locations.append(ResponseParser.normalize_location({
                "country": country,
                "city": city,
                "confidence": confidence,
                "explanation": "Location extracted from truncated response",
            }))

        if not locations:
            return None
        return {"interpretation": interpretation, "locations": locations}

    @staticmethod
    def parse_legacy_format(data: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug("Parsing legacy format response")
        return {
            "interpretation": data.get("interpretation", ""),
            "locations": [ResponseParser.normalize_location(data)],
        }

    @classmethod
    def parse_response(cls, raw_response: str) -> Dict[str, Any]:
        try:
            data = json.loads(cls.clean_json_string(raw_response))
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing failed: {e}")
            repaired = cls._attempt_json_repair(raw_response)
            if repaired:
                logger.info("Recovered partial result from malformed/truncated JSON")
                return repaired
            raise ResponseParsingError(
                f"API response was not valid JSON (possibly truncated): {e}"
            )

        if not isinstance(data, dict):
            raise ResponseParsingError("Response JSON is not an object")

        # Legacy format: a single location at the top level
        if "city" in data and "locations" not in data:
            return cls.parse_legacy_format(data)

        locations = data.get("locations")
        if not isinstance(locations, list):
            raise ResponseParsingError("Response missing 'locations' list")

        normalized = [cls.normalize_location(loc) for loc in locations if cls.validate_location(loc)]
        if not normalized:
            raise ResponseParsingError("No valid locations found in response")

        return {
            "interpretation": str(data.get("interpretation", "")),
            "locations": normalized,
        }
