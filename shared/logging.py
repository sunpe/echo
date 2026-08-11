"""Echo logging configuration independent of Sublime UI objects."""

import logging


LOG = logging.getLogger("echo")
_LOG_FORMAT = "[echo] %(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_log_level(value):
    if not isinstance(value, str):
        return logging.ERROR
    return getattr(logging, value.strip().upper(), logging.ERROR)


def update_log_level(settings):
    level = get_log_level(settings.get("log_level", "ERROR"))
    LOG.setLevel(level)
    LOG.propagate = False
    formatter = logging.Formatter(
        _LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"
    )
    if not LOG.handlers:
        LOG.addHandler(logging.StreamHandler())
    for handler in LOG.handlers:
        handler.setFormatter(formatter)
