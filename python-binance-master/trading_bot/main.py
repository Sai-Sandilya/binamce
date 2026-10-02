# =============================================================
#   ENTRY POINT — Run this file to start the bot
#   Usage:  python main.py
# =============================================================

import sys
import os

# Make sure the trading_bot package imports work from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from bot import run_bot
from grid_bot import run_grid_bot

if __name__ == "__main__":
    mode = getattr(config, "BOT_MODE", "SCREENER").upper()
    if mode == "GRID":
        run_grid_bot()
    else:
        run_bot()

