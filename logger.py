import os
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "server.log")

def setup_logger(name: str = "ai_reviewer") -> logging.Logger:
    """Menyiapkan logger dengan output konsol dan file terotasi otomatis."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Hindari menambahkan handler ganda jika sudah ada
    if not logger.handlers:
        # Format log: Timestamp | Level | [Logger Name] | Pesan
        log_format = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. Handler Konsol (Terminal)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(log_format)
        logger.addHandler(console_handler)

        # 2. Handler File (Rotasi maks 5MB per file, simpan hingga 5 backup file)
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024, # 5 MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)

    return logger

# Instance logger global
logger = setup_logger("ai_reviewer")

