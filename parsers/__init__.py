"""
Instrument file parser architecture for LabLink AI.

This module provides a unified interface for parsing various laboratory
instrument output formats. It uses a plugin-style architecture where
specialized parsers can be added for specific instruments while falling
back to a generic CSV parser for unrecognized formats.

Usage:
    from parsers import detect_format, parse_file, list_supported_formats

    # Detect and parse automatically
    result = parse_file("/path/to/data.csv")

    # Force a specific format
    result = parse_file("/path/to/data.csv", format_hint="generic_csv")

    # List available parsers
    formats = list_supported_formats()
"""

import os
from typing import List, Dict, Optional, Type

from .base import BaseParser, ParsedResult
from .generic_csv import GenericCSVParser
from .agilent_chemstation import AgilentChemStationParser

# Registry of available parsers
# Order matters: specialized parsers should be listed before generic ones
_PARSER_REGISTRY: List[Type[BaseParser]] = [
    # Specialized instrument parsers (checked first)
    AgilentChemStationParser,
    # TecanReaderParser,
    # WatersEmpower,
    # etc.

    # Generic CSV is always last (fallback)
    GenericCSVParser,
]

# Instantiated parsers (lazy initialization)
_parser_instances: Optional[List[BaseParser]] = None


def _get_parsers() -> List[BaseParser]:
    """Get or create parser instances."""
    global _parser_instances
    if _parser_instances is None:
        _parser_instances = [parser_cls() for parser_cls in _PARSER_REGISTRY]
    return _parser_instances


def detect_format(file_path: str) -> str:
    """
    Detect the format of a file and return the appropriate parser name.

    The detection process:
    1. Check file extension against specialized parsers
    2. Use content sniffing to identify format
    3. Fall back to "generic_csv" if no specialized parser matches

    Args:
        file_path: Path to the file or directory to analyze

    Returns:
        Name of the parser that can handle this file (e.g., "generic_csv",
        "agilent_chemstation", etc.)

    Raises:
        FileNotFoundError: If the file/directory doesn't exist
    """
    # Check for file OR directory (some formats like .D are folders)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Path not found: {file_path}")

    parsers = _get_parsers()

    # Try each parser in order (specialized first, generic last)
    for parser in parsers:
        try:
            if parser.detect(file_path):
                return parser.name
        except Exception:
            # If detection fails, try next parser
            continue

    # Should not reach here since GenericCSVParser accepts most files
    return "generic_csv"


def parse_file(
    file_path: str,
    format_hint: Optional[str] = None
) -> ParsedResult:
    """
    Parse a file or data folder and return a standardized result.

    If format_hint is provided, that parser will be used directly.
    Otherwise, the format is auto-detected.

    Args:
        file_path: Path to the file or directory to parse
        format_hint: Optional parser name to use (e.g., "generic_csv")

    Returns:
        ParsedResult containing the parsed data and metadata

    Raises:
        FileNotFoundError: If the file/directory doesn't exist
        ValueError: If the file cannot be parsed or format_hint is invalid
    """
    # Check for file OR directory (some formats like .D are folders)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Path not found: {file_path}")

    parsers = _get_parsers()

    # If format hint provided, find that specific parser
    if format_hint:
        parser = get_parser(format_hint)
        if parser is None:
            raise ValueError(
                f"Unknown format: {format_hint}. "
                f"Available formats: {list_supported_formats()}"
            )
        return parser.parse(file_path)

    # Auto-detect and parse
    for parser in parsers:
        try:
            if parser.detect(file_path):
                return parser.parse(file_path)
        except Exception as e:
            # If parsing fails, try next parser
            continue

    # Last resort: force generic CSV
    generic_parser = get_parser("generic_csv")
    if generic_parser:
        return generic_parser.parse(file_path)

    raise ValueError(f"No parser could handle file: {file_path}")


def get_parser(name: str) -> Optional[BaseParser]:
    """
    Get a parser instance by name.

    Args:
        name: Parser name (e.g., "generic_csv")

    Returns:
        Parser instance or None if not found
    """
    parsers = _get_parsers()
    for parser in parsers:
        if parser.name == name:
            return parser
    return None


def list_supported_formats() -> List[str]:
    """
    List all supported format names.

    Returns:
        List of parser names
    """
    return [parser.name for parser in _get_parsers()]


def get_format_info() -> List[Dict[str, any]]:
    """
    Get detailed information about all supported formats.

    Returns:
        List of dicts with parser details
    """
    return [
        {
            "name": parser.name,
            "description": parser.description,
            "vendor": parser.vendor,
            "extensions": parser.supported_extensions(),
        }
        for parser in _get_parsers()
    ]


def register_parser(parser_cls: Type[BaseParser], priority: int = 0) -> None:
    """
    Register a new parser class.

    Args:
        parser_cls: Parser class to register
        priority: Lower values = higher priority (checked first)
                  Use negative values to prioritize over default parsers

    Example:
        from parsers import register_parser
        from my_parsers import CustomInstrumentParser

        register_parser(CustomInstrumentParser, priority=-1)
    """
    global _parser_instances, _PARSER_REGISTRY

    # Reset instances to force re-initialization
    _parser_instances = None

    # Insert at appropriate position (before GenericCSVParser)
    # GenericCSVParser should always be last
    insert_pos = len(_PARSER_REGISTRY) - 1

    if priority < 0:
        insert_pos = 0
    elif priority < insert_pos:
        insert_pos = priority

    _PARSER_REGISTRY.insert(insert_pos, parser_cls)


# Public API
__all__ = [
    "detect_format",
    "parse_file",
    "list_supported_formats",
    "get_format_info",
    "get_parser",
    "register_parser",
    "ParsedResult",
    "BaseParser",
    "AgilentChemStationParser",
]
