"""Shared dataclasses for the plugin system."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


Severity = Literal["high", "medium", "low"]


@dataclass
class PluginMeta:
    """Hub metadata for a plugin available to install."""
    name: str
    version: str
    url: str
    sha256: str
    author: str = ""
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    tool_count: int = 0
    size_bytes: int = 0
    signature: str | None = None   # optional ed25519 detached signature (hex)


@dataclass
class QualityIssue:
    """Single finding from the auditor."""
    rule: str
    message: str
    severity: Severity
    file: str = ""
    line: int = 0


@dataclass
class QualityReport:
    """Result of a plugin audit run."""
    score: int                      # 0..100
    issues: list[QualityIssue] = field(default_factory=list)
    blockers: list[QualityIssue] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.blockers) or self.score < 60


@dataclass
class InstalledPlugin:
    """Row from the plugins registry DB."""
    name: str
    version: str
    status: Literal["installed", "disabled", "error"]
    score: int
    permissions: list[str]
    install_path: str
    installed_at: str               # ISO-8601 UTC
    last_audited_at: str            # ISO-8601 UTC
    last_error: str | None = None
