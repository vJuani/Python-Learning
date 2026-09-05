"""Voice transcription adapter.

Current path: browser Web Speech via ``JRH.transcribeVoice``.
Server STT (Whisper or similar) is reserved and not implemented.
"""

from __future__ import annotations

PROVIDER_BROWSER = "browser"
PROVIDER_SERVER = "server"


class VoiceSttError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


class VoiceSttClientRequired(VoiceSttError):
    """Browser must transcribe. There is no server audio payload yet."""


class VoiceSttNotImplemented(VoiceSttError):
    """Reserved for a future Whisper / server STT provider."""


def current_stt_provider():
    return PROVIDER_BROWSER


def transcribe_audio(audio=None, *, language="es", provider=None):
    """Do not transcribe on the server yet.

    Home and Agenda share ``JRH.transcribeVoice``. When a server provider
    ships, swap ``current_stt_provider()`` and implement this function.
    """
    chosen = provider or current_stt_provider()
    if chosen == PROVIDER_SERVER:
        raise VoiceSttNotImplemented("voice_stt_server_not_ready")
    raise VoiceSttClientRequired("voice_stt_use_browser")
