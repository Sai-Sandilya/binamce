"""Durable local state for one live trading-bot process.

The exchange remains the source of truth for orders and balances.  This file
only records the bot's intent so a restart can reconcile safely instead of
forgetting a filled position or daily loss limit.
"""

import json
import os
import tempfile
from datetime import date


STATE_PATH = os.path.join(os.path.dirname(__file__), "bot_state.json")


def load_state(path=STATE_PATH):
    """Return validated state, treating a missing/corrupt file as empty."""
    default = {
        "version": 1,
        "date": date.today().isoformat(),
        "daily": {"pnl_pct": 0.0, "trades": 0, "wins": 0, "losses": 0},
        "position": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict):
            return default
        default.update({key: state[key] for key in default if key in state})
        if not isinstance(default["daily"], dict):
            default["daily"] = {"pnl_pct": 0.0, "trades": 0, "wins": 0, "losses": 0}
        return default
    except (OSError, ValueError, TypeError):
        return default


def save_state(state, path=STATE_PATH):
    """Atomically replace state so an interruption cannot leave partial JSON."""
    directory = os.path.dirname(path)
    fd, temporary_path = tempfile.mkstemp(prefix=".bot_state-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
