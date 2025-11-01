"""
Colored logging formatter for improved terminal output scanability.

Provides color-coded log levels to make terminal output easier to read and scan.
"""

import logging
import sys
import colorama
from colorama import Fore, Style

# Initialize colorama for cross-platform support
colorama.init(autoreset=True)


class ColoredFormatter(logging.Formatter):
    """Custom logging formatter that adds colors to log levels."""
    
    # Color mapping for different log levels
    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.MAGENTA + Style.BRIGHT,
    }
    
    def __init__(self, fmt=None, datefmt=None):
        """Initialize the colored formatter with optional format strings."""
        super().__init__(fmt, datefmt)
    
    def format(self, record):
        """Format the log record with appropriate colors."""
        # Get the original formatted message
        log_message = super().format(record)
        
        # Get color for this log level
        color = self.COLORS.get(record.levelname, '')
        
        # Apply color to the entire log message
        if color:
            log_message = f"{color}{log_message}{Style.RESET_ALL}"
        
        return log_message


def add_colored_console_handler(logger, fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'):
    """
    Add a colored console handler to the specified logger.
    
    Args:
        logger: Logger instance to add handler to
        fmt: Log message format string
    
    Returns:
        The created handler
    """
    console_handler = logging.StreamHandler(sys.stdout)
    colored_formatter = ColoredFormatter(fmt=fmt)
    console_handler.setFormatter(colored_formatter)
    logger.addHandler(console_handler)
    return console_handler


def setup_colored_root_logging(log_level=logging.INFO, 
                              fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'):
    """
    Configure root logger with colored output, replacing any existing console handlers.
    
    Args:
        log_level: Logging level (default: INFO)
        fmt: Log message format string
    """
    root_logger = logging.getLogger()
    
    # Remove any existing StreamHandlers to avoid duplicates
    handlers_to_remove = [h for h in root_logger.handlers if isinstance(h, logging.StreamHandler)]
    for handler in handlers_to_remove:
        root_logger.removeHandler(handler)
    
    # Add our colored handler
    add_colored_console_handler(root_logger, fmt)
    root_logger.setLevel(log_level)