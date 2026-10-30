"""Read project-root .env settings, with process environment taking precedence."""
import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_FOLDER = PROJECT_ROOT / "Input"


def read_environment():
    # Use an explicit path so manual startup works from any working directory.
    # Do not execute shell commands or expand references to other variables.
    values = dotenv_values(PROJECT_ROOT / ".env", interpolate=False)
    return {**{key: value for key, value in values.items() if value is not None}, **os.environ}
