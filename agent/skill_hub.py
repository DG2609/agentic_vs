"""
Skill Hub — central registry for discovering and installing community skills.

The Skill Hub is a curated index of ShadowDev markdown skills and Python tool
plugins. It supports:
  - Browsing available skills by category and tag
  - Installing a skill directly from its URL or from the index
  - Version management (upgrade / remove)
  - Safety: skills are plain .md files; plugins are reviewed before listing

Hub index format (JSON at HUB_INDEX_URL):
  {
    "version": 1,
    "skills": [
      {
        "name": "deploy-fly",
        "description": "Deploy project to Fly.io",
        "category": "devops",
        "tags": ["deploy", "flyio", "cloud"],
        "version": "1.0.0",
        "author": "shadowdev",
        "url": "https://raw.githubusercontent.com/.../skills/deploy-fly.md",
        "type": "markdown"   // or "plugin"
      },
      ...
    ]
  }

For self-hosted registries: set HUB_INDEX_URL in config / .env.
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

import config
from agent.skill_engine import SKILLS_DIR

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────

HUB_INDEX_URL = getattr(
    config, "HUB_INDEX_URL",
    "https://raw.githubusercontent.com/shadowdev/shadowdev/main/hub/index.json",
)

# Plugin Python files go here (same location as skill_loader scans)
TOOLS_DIR = SKILLS_DIR / "_tools"

# Maximum file size to download (512 KB — skills should be small)
_MAX_DOWNLOAD_BYTES = 512 * 1024

# Allowed URL schemes for security
_ALLOWED_SCHEMES = {"https", "http"}

_NAME_RE = re.compile(r"^[\w][\w\-]*$")  # safe skill name


# ── Index handling ────────────────────────────────────────────

class HubIndex:
    """Parsed hub index with search and lookup methods."""

    def __init__(self, data: dict):
        self._version = data.get("version", 0)
        self._skills: list[dict] = data.get("skills", [])

    @property
    def skills(self) -> list[dict]:
        return self._skills

    def search(self, query: str = "", category: str = "", tag: str = "") -> list[dict]:
        """Filter skills by name/description query, category, or tag."""
        results = self._skills

        if category:
            results = [s for s in results if s.get("category", "").lower() == category.lower()]

        if tag:
            results = [s for s in results
                       if tag.lower() in [t.lower() for t in s.get("tags", [])]]

        if query:
            q = query.lower()
            results = [
                s for s in results
                if q in s.get("name", "").lower()
                or q in s.get("description", "").lower()
                or any(q in t.lower() for t in s.get("tags", []))
            ]

        return results

    def get(self, name: str) -> Optional[dict]:
        """Look up a skill by exact name."""
        for s in self._skills:
            if s.get("name", "").lower() == name.lower():
                return s
        return None

    @property
    def categories(self) -> list[str]:
        seen: set[str] = set()
        cats = []
        for s in self._skills:
            c = s.get("category", "")
            if c and c not in seen:
                seen.add(c)
                cats.append(c)
        return sorted(cats)


def fetch_index(timeout: int = 10) -> HubIndex:
    """Fetch and parse the hub index. Raises RuntimeError on failure."""
    try:
        resp = requests.get(HUB_INDEX_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return HubIndex(data)
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch hub index from {HUB_INDEX_URL}: {e}") from e
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Hub index is malformed: {e}") from e


# ── Download / install ─────────────────────────────────────────

def _validate_url(url: str) -> None:
    """Raise ValueError if URL is not safe to download from."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Only http/https URLs are allowed, got: {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError(f"URL has no host: {url!r}")


def _download(url: str, timeout: int = 15) -> bytes:
    """Download a URL and return raw bytes. Enforces size limit."""
    _validate_url(url)
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"Skill file exceeds {_MAX_DOWNLOAD_BYTES // 1024} KB size limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException as e:
        raise RuntimeError(f"Download failed from {url}: {e}") from e


def install_skill(
    name: str,
    url: Optional[str] = None,
    overwrite: bool = False,
    timeout: int = 15,
) -> dict:
    """Install a markdown skill from the hub or a direct URL.

    Args:
        name:      Skill name to look up in the hub index (if url not given),
                   or the local filename stem for a direct URL install.
        url:       Direct URL to the .md or .py file. Bypasses index lookup.
        overwrite: Replace an existing skill with the same name.
        timeout:   Network timeout in seconds.

    Returns:
        dict with keys: name, path, version, type, status ("installed"|"updated")

    Raises:
        RuntimeError: network error, hub error, or skill already exists.
        ValueError: bad URL, bad name, or unsupported file type.
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid skill name {name!r}. Use only letters, digits, and hyphens."
        )

    skill_meta: dict = {}

    if url is None:
        # Look up in hub index
        index = fetch_index(timeout=timeout)
        skill_meta = index.get(name) or {}
        if not skill_meta:
            raise RuntimeError(
                f"Skill {name!r} not found in hub. "
                f"Use hub_search() to browse, or provide a direct URL."
            )
        url = skill_meta.get("url", "")
        if not url:
            raise RuntimeError(f"Hub entry for {name!r} has no download URL.")

    skill_type = skill_meta.get("type", "")
    # Auto-detect type from URL if not in metadata
    if not skill_type:
        if url.endswith(".py"):
            skill_type = "plugin"
        elif url.endswith(".md"):
            skill_type = "markdown"
        else:
            raise ValueError(
                f"Cannot determine skill type from URL {url!r}. "
                f"Expected .md or .py extension."
            )

    # Determine destination path
    if skill_type == "plugin":
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        dest = TOOLS_DIR / f"{name}.py"
    else:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        dest = SKILLS_DIR / f"{name}.md"

    if dest.exists() and not overwrite:
        raise RuntimeError(
            f"Skill {name!r} already installed at {dest}. "
            f"Use overwrite=True or skill_install(overwrite=True) to upgrade."
        )

    # Download
    content_bytes = _download(url, timeout=timeout)

    # Basic content sanity check
    if skill_type == "markdown":
        content = content_bytes.decode("utf-8", errors="replace")
        if "---" not in content[:200]:
            logger.warning(
                "[skill_hub] Skill %r may be missing YAML frontmatter — installing anyway",
                name,
            )
    elif skill_type == "plugin":
        content = content_bytes.decode("utf-8", errors="replace")
        if "__skill_tools__" not in content:
            raise ValueError(
                f"Plugin {name!r} is missing __skill_tools__ — refusing to install."
            )

    status = "updated" if dest.exists() else "installed"
    dest.write_bytes(content_bytes)

    sha = hashlib.sha256(content_bytes).hexdigest()[:12]
    logger.info(
        "[skill_hub] %s skill %r → %s (sha256: %s)", status, name, dest, sha
    )

    return {
        "name": name,
        "path": str(dest),
        "version": skill_meta.get("version", "unknown"),
        "type": skill_type,
        "status": status,
        "sha256": sha,
    }


def remove_skill(name: str) -> dict:
    """Remove an installed skill (markdown or plugin).

    Returns dict with name, path, status.
    Raises RuntimeError if not found.
    """
    removed: list[str] = []

    for candidate in [
        SKILLS_DIR / f"{name}.md",
        TOOLS_DIR / f"{name}.py",
    ]:
        if candidate.exists():
            candidate.unlink()
            removed.append(str(candidate))
            logger.info("[skill_hub] Removed skill %r from %s", name, candidate)

    if not removed:
        raise RuntimeError(f"Skill {name!r} not found (checked {SKILLS_DIR} and {TOOLS_DIR}).")

    return {"name": name, "removed": removed, "status": "removed"}


def list_installed() -> list[dict]:
    """Return metadata for all locally installed skills (markdown + plugins)."""
    results = []

    if SKILLS_DIR.exists():
        for f in sorted(SKILLS_DIR.glob("*.md")):
            if f.name.startswith("_"):
                continue
            results.append({
                "name": f.stem,
                "type": "markdown",
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })

    if TOOLS_DIR.exists():
        for f in sorted(TOOLS_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            results.append({
                "name": f.stem,
                "type": "plugin",
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })

    return results
