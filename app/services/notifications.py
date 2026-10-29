import logging

logger = logging.getLogger(__name__)


def notify_upload(owner_email: str, filenames: list[str]) -> None:
    """Post-upload notification hook; replace logger with an email/event provider."""
    logger.info("Documents uploaded for %s: %s", owner_email, filenames)
