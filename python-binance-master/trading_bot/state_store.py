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
    """Load state without ever silently treating corruption as a clean start."""
    default = {
        "version": 1,
        "date": date.today().isoformat(),
        "daily": {"pnl_pct": 0.0, "trades": 0, "wins": 0, "losses": 0},
        "position": None,
        "pending_buy": None,
        "load_error": None,
    }
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict):
            default["load_error"] = "state root is not an object"
            return default
        default.update({key: state[key] for key in default if key in state})
        if not isinstance(default["daily"], dict):
            default["load_error"] = "daily state is invalid"
        return default
    except FileNotFoundError:
        return default
    except (OSError, ValueError, TypeError) as exc:
        default["load_error"] = f"state unreadable: {exc}"
        return default


def save_state(state, path=STATE_PATH):
    """Atomically replace state so an interruption cannot leave partial JSON."""
    directory = os.path.dirname(path)
    fd, temporary_path = tempfile.mkstemp(prefix=".bot_state-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            saved_state = dict(state)
            saved_state["load_error"] = None
            json.dump(saved_state, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
