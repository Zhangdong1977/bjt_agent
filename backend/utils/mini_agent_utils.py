"""Mini-Agent utility functions."""

import sys
from pathlib import Path


def setup_mini_agent_path() -> None:
    """Add Mini-Agent to sys.path if not already present.

    This should be called before importing any mini_agent modules.
    The Mini-Agent submodule is located at the project root.
    """
    mini_agent_path = Path(__file__).parent.parent.parent / "Mini-Agent"
    if mini_agent_path.exists() and str(mini_agent_path) not in sys.path:
        sys.path.insert(0, str(mini_agent_path))


# Automatically setup path when this module is imported
setup_mini_agent_path()
