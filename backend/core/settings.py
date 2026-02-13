from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]  # .../backend
# Load .env from project root (next to original main.py) if present
# You can change this to wherever your .env lives.
load_dotenv(BASE_DIR.parent / ".env")

NETBOX_URL: str = os.getenv("NETBOX_URL", "http://localhost:8000")

SSH_USERNAME: str | None = os.getenv("SSH_USERNAME")
SSH_PASSWORD: str | None = os.getenv("SSH_PASSWORD")
NETBOX_TOKEN: str | None = os.getenv("NETBOX_TOKEN")

NETBOX_HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
}
