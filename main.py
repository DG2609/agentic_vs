"""
Entry point for the Agentic IDE Backend.
Redirects to the new Socket.IO + aiohttp server in server/main.py.
"""
import sys
import os
import logging
from aiohttp import web

# Ensure server module can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.main import create_app
import config

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"🚀 Starting Agentic IDE Backend (Socket.IO + aiohttp)...")
    web.run_app(create_app(), host=config.HOST, port=config.PORT)
