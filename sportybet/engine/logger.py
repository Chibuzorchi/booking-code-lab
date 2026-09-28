"""Small coloured logger, in the spirit of automation_newgen's infra.logger."""
import logging
import sys

_COLORS = {
    "DEBUG": "\033[0;36m",
    "INFO": "\033[0;34m",
    "SUCCESS": "\033[0;32m",
    "WARNING": "\033[0;33m",
    "ERROR": "\033[0;31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"
SUCCESS_LEVEL = 21
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


class _Formatter(logging.Formatter):
    def __init__(self, color: bool = True):
        super().__init__("%(asctime)s [%(levelname)-7s] %(name)-14s %(message)s", "%H:%M:%S")
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.color:
            c = _COLORS.get(record.levelname, "")
            if c:
                text = f"{c}{text}{_RESET}"
        return text


def get_logger(name: str = "bet9ja", level: str = "INFO", color: bool = True) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_Formatter(color=color))
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    def success(msg, *args, **kwargs):
        if logger.isEnabledFor(SUCCESS_LEVEL):
            logger.log(SUCCESS_LEVEL, msg, *args, **kwargs)

    logger.success = success  # type: ignore[attr-defined]
    return logger
