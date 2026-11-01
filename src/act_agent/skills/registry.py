"""Discovers `SKILL.md` packages (YAML frontmatter + markdown body + references/scripts)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str
    references: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    entry: bool = False  # group router skill (act-mcp-big-activity)

    def reference_path(self, rel: str) -> Path | None:
        return _safe_child(self.path / "references", rel)

    def script_path(self, rel: str) -> Path | None:
        return _safe_child(self.path / "scripts", rel)


def _safe_child(base: Path, rel: str) -> Path | None:
    """Resolve `rel` under `base`, rejecting traversal and absolute paths."""
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        return None
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class SkillRegistry:
    ENTRY_SKILL = "act-mcp-big-activity"

    def __init__(self, root: Path) -> None:
        self.root = root
        self._skills: dict[str, Skill] = {}

    def discover(self) -> list[Skill]:
        self._skills.clear()
        if not self.root.is_dir():
            logger.warning("skills dir not found: %s", self.root)
            return []
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            try:
                skill = self._parse(skill_md)
            except Exception as exc:  # noqa: BLE001 - one bad skill must not kill startup
                logger.error("failed to parse %s: %s", skill_md, exc)
                continue
            self._skills[skill.name] = skill
        logger.info("discovered %d skills: %s", len(self._skills), ", ".join(self._skills))
        return list(self._skills.values())

    def _parse(self, skill_md: Path) -> Skill:
        raw = skill_md.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(raw)
        if not match:
            raise ValueError("missing YAML frontmatter")
        meta = yaml.safe_load(match.group(1)) or {}
        name = str(meta.get("name") or skill_md.parent.name).strip()
        description = str(meta.get("description") or "").strip()
        if not name:
            raise ValueError("missing skill name")
        skill_dir = skill_md.parent
        refs = tuple(
            str(p.relative_to(skill_dir / "references"))
            for p in sorted((skill_dir / "references").rglob("*"))
            if p.is_file()
        ) if (skill_dir / "references").is_dir() else ()
        scripts = tuple(
            str(p.relative_to(skill_dir / "scripts"))
            for p in sorted((skill_dir / "scripts").rglob("*"))
            if p.is_file()
        ) if (skill_dir / "scripts").is_dir() else ()
        return Skill(
            name=name,
            description=description,
            path=skill_dir,
            body=raw[match.end():].strip(),
            references=refs,
            scripts=scripts,
            entry=name == self.ENTRY_SKILL,
        )

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str | None) -> Skill | None:
        return self._skills.get(name) if name else None

    @property
    def route_targets(self) -> list[Skill]:
        """Exclude the group router: our own router plays that role."""
        return [s for s in self._skills.values() if not s.entry]
