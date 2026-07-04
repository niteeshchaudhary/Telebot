import logging
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback

from app.config import settings


def setup_logging() -> logging.Logger:
    install_rich_traceback(show_locals=True)

    console = Console(stderr=True)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                tracebacks_show_locals=True,
                markup=True,
            )
        ],
    )

    logger = logging.getLogger("telebot")
    logger.setLevel(getattr(logging, settings.log_level.upper()))

    for name in ["sqlalchemy.engine", "sqlalchemy.pool", "telegram", "httpx"]:
        logging.getLogger(name).setLevel(logging.WARNING)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"telebot.{name}")


class StructuredLogger:
    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        extra = {"extra_data": kwargs} if kwargs else {}
        self._logger.log(level, message, extra=extra)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        self._logger.exception(message, extra={"extra_data": kwargs} if kwargs else {})


logger = setup_logging()
