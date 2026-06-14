import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest
from unittest.mock import patch, MagicMock


def test_transcribe_returns_text():
    mock_trans = MagicMock()
    mock_trans.text = "Quiero una cita para mañana"

    with patch("httpx.get") as mock_get, \
         patch("openai.OpenAI") as mock_openai:
        mock_get.return_value.content = b"fake_audio"
        mock_openai.return_value.audio.transcriptions.create.return_value = mock_trans

        import utils.audio
        utils.audio._openai = None  # reset singleton

        from utils.audio import transcribe_audio
        result = transcribe_audio("https://example.com/audio.ogg")
        assert result == "Quiero una cita para mañana"


def test_transcribe_passes_spanish_language():
    mock_trans = MagicMock()
    mock_trans.text = "Hola"

    with patch("httpx.get") as mock_get, \
         patch("openai.OpenAI") as mock_openai:
        mock_get.return_value.content = b"fake_audio"
        create_mock = mock_openai.return_value.audio.transcriptions.create
        create_mock.return_value = mock_trans

        import utils.audio
        utils.audio._openai = None  # reset singleton

        from utils.audio import transcribe_audio
        transcribe_audio("https://example.com/audio.ogg")

        call_kwargs = create_mock.call_args[1]
        assert call_kwargs.get("language") == "es"
        assert call_kwargs.get("model") == "whisper-1"
