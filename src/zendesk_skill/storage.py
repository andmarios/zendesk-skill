"""Response storage and structure extraction for efficient LLM context management."""

import hashlib
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Default storage directory (cross-platform)
DEFAULT_STORAGE_DIR = Path(tempfile.gettempdir()) / "zendesk-skill"


def _get_storage_dir(ticket_id: Optional[str] = None) -> Path:
    """Get and ensure storage directory exists.

    Args:
        ticket_id: Optional ticket ID to organize files by ticket

    Returns:
        Path to storage directory
    """
    if ticket_id:
        storage_dir = DEFAULT_STORAGE_DIR / str(ticket_id)
    else:
        storage_dir = DEFAULT_STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _generate_filename(tool_name: str, params: dict[str, Any]) -> str:
    """Generate a unique filename for a response.

    Format: {tool}_{md5_8chars}_{timestamp}.json
    """
    # Create hash from parameters
    params_str = json.dumps(params, sort_keys=True)
    hash_str = hashlib.md5(params_str.encode()).hexdigest()[:8]

    # Unix timestamp
    timestamp = int(time.time())

    return f"{tool_name}_{hash_str}_{timestamp}.json"


def _extract_type_description(value: Any, max_depth: int = 3, current_depth: int = 0) -> str:
    """Extract a type description for a value.

    Provides a human-readable description of the type and structure.
    """
    if current_depth >= max_depth:
        return "..."

    if value is None:
        return "null"
    elif isinstance(value, bool):
        return "boolean"
    elif isinstance(value, int):
        return "integer"
    elif isinstance(value, float):
        return "number"
    elif isinstance(value, str):
        # Detect special string types
        if value.startswith("http://") or value.startswith("https://"):
            return "url"
        elif "@" in value and "." in value:
            return "email"
        elif len(value) == 10 and value.count("-") == 2:
            return "date (YYYY-MM-DD)"
        elif "T" in value and ("Z" in value or "+" in value):
            return "datetime (ISO 8601)"
        elif len(value) > 100:
            return "long string"
        else:
            return "string"
    elif isinstance(value, list):
        if not value:
            return "array (empty)"
        # Sample first item
        item_type = _extract_type_description(value[0], max_depth, current_depth + 1)
        return f"array[{item_type}] ({len(value)} items)"
    elif isinstance(value, dict):
        if not value:
            return "object (empty)"
        return "object"
    else:
        return type(value).__name__


def _extract_structure(
    data: Any,
    max_depth: int = 4,
    current_depth: int = 0,
    prefix: str = "",
) -> dict[str, str]:
    """Extract the structure of a data object.

    Returns a flat dict mapping dot-notation paths to type descriptions.
    Useful for understanding API response structure without reading all data.
    """
    structure: dict[str, str] = {}

    if current_depth >= max_depth:
        return structure

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict) and value:
                structure[path] = "object"
                nested = _extract_structure(value, max_depth, current_depth + 1, path)
                structure.update(nested)
            elif isinstance(value, list):
                if not value:
                    structure[path] = "array (empty)"
                else:
                    first = value[0]
                    if isinstance(first, dict):
                        structure[path] = f"array[object] ({len(value)} items)"
                        nested = _extract_structure(first, max_depth, current_depth + 1, f"{path}[]")
                        structure.update(nested)
                    else:
                        item_type = _extract_type_description(first)
                        structure[path] = f"array[{item_type}] ({len(value)} items)"
            else:
                structure[path] = _extract_type_description(value)

    return structure


def _count_items(data: Any) -> int:
    """Count the number of items in a response.

    Handles common Zendesk response patterns like tickets[], users[], comments[].
    """
    if isinstance(data, dict):
        # Check for common list keys
        for key in ["tickets", "users", "comments", "results", "organizations", "groups", "views", "satisfaction_ratings"]:
            if key in data and isinstance(data[key], list):
                return len(data[key])
        # Check for single item patterns
        for key in ["ticket", "user", "comment", "organization", "group", "view"]:
            if key in data:
                return 1
    elif isinstance(data, list):
        return len(data)
    return 0


def save_response(
    tool_name: str,
    params: dict[str, Any],
    data: Any,
    suggested_queries: Optional[list[dict[str, str]]] = None,
    output_path: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Save an API response to a local file with metadata.

    Args:
        tool_name: Name of the tool that made the request
        params: Parameters passed to the tool
        data: API response data
        suggested_queries: List of suggested jq queries for this response
        output_path: Optional custom output path (overrides default and ticket_id)
        ticket_id: Optional ticket ID to organize files by ticket

    Returns:
        Tuple of (file_path, stored_data)
    """
    # Determine output path
    if output_path:
        file_path = Path(output_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        storage_dir = _get_storage_dir(ticket_id)
        filename = _generate_filename(tool_name, params)
        file_path = storage_dir / filename

    # Extract structure
    structure = _extract_structure(data)

    # Build stored data
    stored_data = {
        "metadata": {
            "tool": tool_name,
            "params": params,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "itemCount": _count_items(data),
            "filePath": str(file_path),
        },
        "structure": structure,
        "suggestedQueries": suggested_queries or [],
        "data": data,
    }

    # Write to file
    with open(file_path, "w") as f:
        json.dump(stored_data, f, indent=2, default=str)

    return str(file_path), stored_data


def load_response(file_path: str) -> dict[str, Any]:
    """Load a previously saved response.

    Args:
        file_path: Path to the saved response file

    Returns:
        The stored data dict

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
    """
    with open(file_path) as f:
        return json.load(f)


def format_save_result(file_path: str, stored_data: dict[str, Any]) -> str:
    """Format a save result for display.

    Returns a human-readable summary suitable for tool output.
    """
    metadata = stored_data.get("metadata", {})
    structure = stored_data.get("structure", {})
    suggested = stored_data.get("suggestedQueries", [])

    lines = [
        f"**Response saved to:** `{file_path}`",
        "",
        f"**Items:** {metadata.get('itemCount', 0)}",
        f"**Timestamp:** {metadata.get('timestamp', 'unknown')}",
        "",
        "**Structure:**",
    ]

    # Show structure (limited to key fields)
    for path, type_desc in list(structure.items())[:15]:
        lines.append(f"  - `{path}`: {type_desc}")

    if len(structure) > 15:
        lines.append(f"  - ... and {len(structure) - 15} more fields")

    if suggested:
        lines.append("")
        lines.append("**Suggested queries:**")
        for q in suggested[:5]:
            lines.append(f"  - `{q['name']}`: {q['description']}")

    return "\n".join(lines)
