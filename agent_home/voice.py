"""Private, workspace-local voice attachments for the assigned agent to decode."""

from .media import save_attachment


MAX_VOICE_BYTES = 20_000_000


class VoiceError(ValueError):
    """A safe, user-facing explanation for a voice input that was not submitted."""


def save_voice(cwd, job_id, audio, mime_type=None):
    if not cwd:
        raise VoiceError("The sleeve has no working folder in which to save the recording")
    if not 0 < len(audio) <= MAX_VOICE_BYTES:
        raise VoiceError("Voice recording is empty or exceeds the 20 MB download limit")
    extension = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}.get(mime_type, ".ogg")
    return save_attachment(cwd, job_id, audio, "voice", extension)


def voice_instruction(path):
    return (f"The user sent a Telegram voice note saved at: {path}\n\n"
            "Transcribe this audio using available local tools. Begin your first reply with exactly: "
            "You asked: <transcribed text>. Then respond to or carry out the spoken request. "
            "If you cannot decode it, explain what tool is missing.")
