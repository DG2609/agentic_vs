# ShadowDev External Skill & Plugin Marketplace — Design Spec

**Date:** 2026-04-16  
**Status:** Approved for implementation  
**Author:** Self-selected design (trust B, UX C, sources C)

---

## 1. Problem Statement

ShadowDev currently supports skills (markdown workflow files) and plugins (Python tool packages) but discovery and installation are manual or locked to a single hardcoded GitHub URL. There is no user-facing marketplace, no multi-registry support, no direct URL install, and no community contribution path.

**Goal:** Make it trivially easy to find, install, and manage skills and plugins from official and community sources, with clear trust signals and no new security regressions.

---

## 2. Design Decisions (pre-selected)

| Dimension | Choice | Rationale |
|---|---|---|
| Install sources | Registry + URL (Option C) | Mirrors pip: PyPI + `pip install git+...` |
| UX | CLI commands + TUI panel (Option C) | Power users get slash commands; others get VS Code-style browser |
| Trust model | Signed registry (Option B) | Verified authors in registry index; unverified sources get explicit warning |
| Sandbox | None (P2) | Subprocess sandboxing adds complexity without clear MVP benefit |

---

## 3. Architecture

```
agent/marketplace/
  __init__.py          — public API: install_skill, install_plugin, search, etc.
  registry.py          — multi-registry: load, cache, merge, search JSON indexes
  installer.py         — download, SHA256-verify, install skill/plugin to disk
  trust.py             — verified_authors set + trust-level classification
  commands.py          — slash command handlers (/skill, /plugin, /registry, /marketplace)

ink-cli/src/
  components/
    MarketplacePanel.tsx   — TUI browser: search, categories, install
  hooks/
    useMarketplace.ts      — data fetching hook for MarketplacePanel

agent/
  skill_hub.py         — thin wrapper (kept for backward compat, delegates to marketplace/)
  plugin_registry.py   — kept as-is (pip entry_points discovery — still the install mechanism)

config.py              — add HUB_REGISTRIES: list[str] setting
server/main.py         — add /api/marketplace/* endpoints
cli.py                 — add /skill, /plugin, /registry, /marketplace slash commands
```

---

## 4. Registry Protocol

### Index format (version 2 — backward compatible with v1)

```json
{
  "version": 2,
  "name": "ShadowDev Official Registry",
  "url": "https://raw.githubusercontent.com/shadowdev-hub/registry/main/index.json",
  "verified_authors": ["shadowdev", "anthropic-labs", "community-verified"],
  "skills": [
    {
      "name": "deploy-fly",
      "display_name": "Deploy to Fly.io",
      "description": "One-command deployment workflow for Fly.io",
      "category": "devops",
      "tags": ["deploy", "cloud", "flyio"],
      "version": "1.2.0",
      "author": "shadowdev",
      "url": "https://raw.githubusercontent.com/shadowdev-hub/skills/main/deploy-fly.md",
      "sha256": "abc123def456...",
      "type": "markdown",
      "license": "MIT",
      "min_shadowdev": "1.0.0"
    }
  ],
  "plugins": [
    {
      "name": "shadowdev-plugin-db",
      "display_name": "Database Tools",
      "description": "SQL query, schema inspect, migration tools",
      "pypi": "shadowdev-plugin-db",
      "version": "0.3.1",
      "author": "community-verified",
      "sha256": "789abc...",
      "access": "write",
      "license": "MIT",
      "min_shadowdev": "1.0.0"
    }
  ]
}
```

### Version 1 compatibility
`registry.py` treats v1 indexes as v2 with empty `verified_authors` and no `sha256` fields — all entries marked unverified.

---

## 5. Install Sources

| Source | Syntax | Trust Level |
|---|---|---|
| Official registry | `/skill install deploy-fly` | Verified if author in `verified_authors` |
| Custom registry | `/skill install myregistry:skill-name` | Depends on that registry's `verified_authors` |
| Direct HTTPS URL | `/skill install https://example.com/skill.md` | Always Unverified |
| GitHub shorthand | `/skill install github:user/repo/skills/foo.md` | Always Unverified |
| PyPI (plugins) | `/plugin install shadowdev-plugin-db` | Registry-verified if in index |
| GitHub pip (plugins) | `/plugin install github:user/repo` | Always Unverified |

### GitHub shorthand resolution
`github:user/repo/path/to/skill.md` → `https://raw.githubusercontent.com/user/repo/main/path/to/skill.md`  
`github:user/repo/path/to/skill.md@v1.2` → uses the `v1.2` branch/tag

---

## 6. Trust Model

### Trust levels

| Level | Badge | Criteria | Install behavior |
|---|---|---|---|
| `verified` | ✓ Verified | Author in registry's `verified_authors` AND sha256 present and matches | Show info, ask `Install? [y/N]` |
| `unverified` | ⚠ Unverified | Author not in verified_authors OR URL install | Show yellow warning, require explicit `yes` |
| `plugin-write` | ⚠ Write access | Any plugin with `access: "write"` | Extra warning: "This plugin can write files and run commands" |

### SHA256 verification
- For registry installs: download file, compute SHA256, compare to index entry. Reject if mismatch.
- For URL installs: compute SHA256 after download, display to user (no expected value to compare against).
- Stored in `.shadowdev/installed.json` alongside install metadata.

### installed.json format
```json
{
  "skills": {
    "deploy-fly": {
      "version": "1.2.0",
      "source": "registry:official",
      "sha256": "abc123...",
      "author": "shadowdev",
      "trust": "verified",
      "installed_at": "2026-04-16T10:00:00Z"
    }
  },
  "plugins": {
    "shadowdev-plugin-db": {
      "version": "0.3.1",
      "source": "pypi",
      "author": "community-verified",
      "trust": "verified",
      "installed_at": "2026-04-16T10:00:00Z"
    }
  }
}
```

---

## 7. CLI Commands

### /skill commands
```
/skill list                     List installed skills
/skill search <query>           Search all configured registries
/skill install <name>           Install skill from registry
/skill install <url>            Install from direct HTTPS URL
/skill install github:<path>    Install from GitHub (user/repo/path[@ref])
/skill remove <name>            Uninstall skill
/skill update [name]            Update one or all installed skills
/skill info <name>              Show metadata (version, author, trust, sha256)
```

### /plugin commands
```
/plugin list                    List installed plugins (pip + entry_points)
/plugin search <query>          Search registries for plugins
/plugin install <name>          Install from registry (pip install)
/plugin install github:<path>   Install from GitHub (pip install git+...)
/plugin remove <name>           Uninstall (pip uninstall)
/plugin info <name>             Show metadata + access level
```

### /registry commands
```
/registry list                  Show all configured registries + status
/registry add <url>             Add a custom registry
/registry remove <url>          Remove a custom registry
/registry sync                  Refresh all registry indexes (force re-fetch)
```

### /marketplace
```
/marketplace                    Open TUI browser panel (ink-cli) or text list (Python CLI)
```

---

## 8. Multi-Registry Support

### Configuration
`config.py` adds:
```python
HUB_REGISTRIES: list[str] = Field(
    default_factory=lambda: [
        "https://raw.githubusercontent.com/shadowdev-hub/registry/main/index.json"
    ],
    description="List of registry index URLs. First = highest priority."
)
```

User adds custom registries via `/registry add <url>` — persisted to `.shadowdev/registries.json`.

### Merge strategy
When searching across multiple registries:
- Results are union-merged, deduplicated by `(name, type)`
- Higher-priority registry wins on name collision
- Trust level comes from the registry that provides the entry

### Caching
Registry indexes are cached in `.shadowdev/registry-cache/<hash>.json` with a 1-hour TTL. `sync` busts the cache.

---

## 9. TUI Marketplace Panel (ink-cli: MarketplacePanel.tsx)

### Layout
```
┌─ Marketplace ─────────────────────────────────────┐
│ [Skills] [Plugins] [Installed] [Registries]        │
│ Search: ____________                               │
├────────────────────────────────────────────────────┤
│ ✓ deploy-fly       v1.2.0   shadowdev   devops     │
│   Deploy to Fly.io in one command                  │
│                                                    │
│ ⚠ community-skill  v0.1.0   unknown    misc        │
│   Does something (unverified)                      │
│                                                    │
│ [↑↓ navigate] [Enter: details] [i: install]        │
│ [r: remove] [u: update] [/: search] [q: close]     │
└────────────────────────────────────────────────────┘
```

### Tabs
- **Skills** — list from all registries, filterable by category/tag
- **Plugins** — list from all registries
- **Installed** — skills + plugins currently installed (from `installed.json`)
- **Registries** — list/add/remove registry URLs

### Install flow in TUI
1. User selects item, presses `i`
2. Panel shows trust badge + SHA256 + description
3. For unverified: shows `⚠ Unverified source. Type "yes" to confirm:`
4. Progress indicator during download
5. Success/error message inline

### API backend
`server/main.py` adds:
```
GET  /api/marketplace/search?q=&type=skill|plugin&registry=
GET  /api/marketplace/info?name=&type=skill|plugin
POST /api/marketplace/install   { name, type, source? }
POST /api/marketplace/remove    { name, type }
GET  /api/marketplace/installed
GET  /api/marketplace/registries
POST /api/marketplace/registries/add    { url }
POST /api/marketplace/registries/remove { url }
POST /api/marketplace/sync
```

---

## 10. Implementation Phases

### Phase 1 — Core marketplace module (agent/marketplace/)
- `registry.py`: multi-registry load, cache, merge, search
- `installer.py`: download, SHA256 verify, install skill to disk + pip install for plugins
- `trust.py`: trust level classification
- `__init__.py`: clean public API

### Phase 2 — CLI integration
- Python CLI (`cli.py`): `/skill`, `/plugin`, `/registry`, `/marketplace` commands
- `server/main.py`: `/api/marketplace/*` REST endpoints
- ink-cli (`App.tsx`): add slash command handlers for `/skill`, `/plugin`, `/registry`, `/marketplace`

### Phase 3 — TUI panel
- `MarketplacePanel.tsx`: full browser panel
- `useMarketplace.ts`: data hook for panel
- Wire `/marketplace` command in ink-cli to open panel

### Phase 4 — Official registry
- Create `shadowdev-hub/registry` GitHub repo with `index.json`
- Seed with 10-20 initial skills from existing ShadowDev skill library
- Document contribution process (PR to registry repo)

---

## 11. Files to Create / Modify

### New files
```
agent/marketplace/__init__.py
agent/marketplace/registry.py
agent/marketplace/installer.py
agent/marketplace/trust.py
agent/marketplace/commands.py
ink-cli/src/components/MarketplacePanel.tsx
ink-cli/src/hooks/useMarketplace.ts
```

### Modified files
```
config.py                     — add HUB_REGISTRIES setting
agent/skill_hub.py            — thin wrapper delegating to marketplace/registry.py
cli.py                        — add /skill /plugin /registry /marketplace handlers
server/main.py                — add /api/marketplace/* endpoints
ink-cli/src/App.tsx           — add /marketplace /skill /plugin /registry commands
```

---

## 12. Out of Scope (P2+)

- Subprocess sandboxing for plugins
- Plugin ratings / review system
- Automatic update checks on startup
- Private registry auth (token-based)
- Skill/plugin publishing workflow (CI pipeline)
- Version pinning / lockfiles

---

## 13. Success Criteria

- `/skill install deploy-fly` works end-to-end in under 5 seconds
- Trust warnings appear for all unverified sources before install
- `/marketplace` opens TUI panel with search in < 1s (cached index)
- Custom registry URL can be added and searched without restart
- All install metadata persisted in `installed.json` and survives restart
- Existing `skill_hub.py` callers continue working unchanged
