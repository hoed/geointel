import pytest

from geointel.api_client import GeminiClient
from geointel.exceptions import APIError, InvalidImageError, ResponseParsingError
from geointel.image_processor import ImageProcessor
from geointel.response_parser import ResponseParser


def test_extract_text_skips_thought_parts():
    data = {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": '{"a":1}'}]}}]}
    assert GeminiClient._extract_response_text(data) == '{"a":1}'


def test_extract_text_blocked_prompt():
    with pytest.raises(APIError):
        GeminiClient._extract_response_text({"promptFeedback": {"blockReason": "SAFETY"}})


def test_extract_text_empty_reports_finish_reason():
    with pytest.raises(APIError, match="MAX_TOKENS"):
        GeminiClient._extract_response_text(
            {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]})


def test_parser_handles_fences_prose_and_bad_types():
    raw = 'Here you go:\n```json\n{"interpretation":"x","locations":[{"country":"ID","city":"Jakarta","confidence":null,"coordinates":{"latitude":"-6.2","longitude":"106.8"}}]}\n```'
    loc = ResponseParser.parse_response(raw)["locations"][0]
    assert loc["confidence"] == "Medium"
    assert loc["coordinates"] == {"latitude": -6.2, "longitude": 106.8}


def test_parser_rejects_out_of_range_coordinates():
    loc = ResponseParser.normalize_location(
        {"country": "A", "city": "B", "confidence": "High", "coordinates": {"latitude": 999, "longitude": 0}})
    assert loc["coordinates"] == {"latitude": 0.0, "longitude": 0.0}


def test_parser_recovers_truncated_json():
    raw = '{"interpretation":"abc","locations":[{"country":"Japan","city":"Tokyo","confidence":"High","coordinates":{"latitude":35.6,"longi'
    res = ResponseParser.parse_response(raw)
    assert res["locations"][0]["city"] == "Tokyo"


def test_parser_non_object_json():
    with pytest.raises(ResponseParsingError):
        ResponseParser.parse_response("[1,2,3]")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.jpg", "http://localhost/x.jpg",
    "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/x.jpg",
])
def test_download_blocks_private_hosts(url):
    with pytest.raises(InvalidImageError):
        ImageProcessor.download_image(url)


def test_mime_detection():
    assert ImageProcessor.detect_mime_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert ImageProcessor.detect_mime_type(b"RIFF\0\0\0\0WEBPxxxx") == "image/webp"


def test_falls_back_to_other_model_when_overloaded(monkeypatch):
    import geointel.api_client as ac

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
        def json(self):
            return self._b

    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "gemini-3.8-flash" in url:
            return Resp(503, {"error": {"message": "high demand"}})
        return Resp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(ac.requests, "post", fake_post)
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    client = ac.GeminiClient(api_key="k", model="gemini-3.8-flash")
    assert client.generate_content("p", "b64") == "ok"
    assert "gemini-3.8-flash" in calls[0] and "gemini-3.8-flash" not in calls[-1]
