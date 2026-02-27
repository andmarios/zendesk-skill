"""Time-related utility functions."""


def mins_to_human(mins: float | None) -> str:
    """Convert minutes to human-readable format."""
    if mins is None:
        return "N/A"
    if mins < 60:
        return f"{int(mins)}m"
    elif mins < 1440:
        return f"{mins/60:.1f}h"
    else:
        return f"{mins/1440:.1f}d"
