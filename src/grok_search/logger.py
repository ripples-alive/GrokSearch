import logging
import sys
from datetime import datetime
from pathlib import Path
from .config import config

logger = logging.getLogger("grok_search")
logger.setLevel(getattr(logging, config.log_level, logging.INFO))
logger.propagate = False

_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

if not any(isinstance(handler, logging.StreamHandler) and getattr(handler, "stream", None) in {sys.stderr, sys.stdout} for handler in logger.handlers):
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(getattr(logging, config.log_level, logging.INFO))
    stream_handler.setFormatter(_formatter)
    logger.addHandler(stream_handler)

try:
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"grok_search_{datetime.now().strftime('%Y%m%d')}.log"

    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_file for handler in logger.handlers):
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(getattr(logging, config.log_level, logging.INFO))
        file_handler.setFormatter(_formatter)
        logger.addHandler(file_handler)
except OSError:
    pass

async def log_info(ctx, message: str, is_debug: bool = False):
    if is_debug:
        logger.info(message)
        
    if ctx:
        await ctx.info(message)


async def log_exception(ctx, message: str, exc: Exception, is_debug: bool = False):
    logger.error("%s: %s", message, exc, exc_info=is_debug)
    if ctx and is_debug:
        await ctx.error(f"{message}: {exc}")
