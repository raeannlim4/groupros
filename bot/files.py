"""Download files from Telegram with longer timeouts and one retry."""

from __future__ import annotations

import asyncio
import logging

from telegram.error import NetworkError, TimedOut

logger = logging.getLogger(__name__)

READ_TIMEOUT = 60.0
CONNECT_TIMEOUT = 30.0
RETRY_DELAY = 2.0

DOWNLOAD_FAILED = (
    "I couldn't download that file from Telegram (network timeout). "
    "Please try sending it again."
)


class FileDownloadError(Exception):
    """The file could not be downloaded from Telegram after a retry."""

    def __init__(self, message: str = DOWNLOAD_FAILED) -> None:
        super().__init__(message)
        self.message = message


async def download_file(context, file_id: str) -> bytes:
    """Download a Telegram file, retrying once after a network timeout.

    ``get_file`` and the download both use longer timeouts because large files
    over a slow link regularly exceed the library's short defaults. A single
    retry covers a transient ``TimedOut``/``NetworkError``.
    """
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            tg_file = await context.bot.get_file(
                file_id,
                read_timeout=READ_TIMEOUT,
                connect_timeout=CONNECT_TIMEOUT,
            )
            data = await tg_file.download_as_bytearray(
                read_timeout=READ_TIMEOUT,
                connect_timeout=CONNECT_TIMEOUT,
            )
            return bytes(data)
        except (TimedOut, NetworkError) as exc:
            last_error = exc
            logger.warning(
                "Telegram file download failed (attempt %d/2): %s", attempt + 1, exc
            )
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
    raise FileDownloadError(DOWNLOAD_FAILED) from last_error
