import os
import io
import httpx
import openai

_openai = None


def _get_openai():
    global _openai
    if _openai is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _openai = openai.OpenAI(api_key=key)
    return _openai


def transcribe_audio(audio_url: str) -> str:
    response = httpx.get(audio_url, timeout=30)
    response.raise_for_status()
    buf = io.BytesIO(response.content)
    buf.name = "audio.ogg"
    transcription = _get_openai().audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        language="es"
    )
    return transcription.text
