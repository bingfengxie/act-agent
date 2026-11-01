"""Expose skill resources to the model: read a reference, run a bundled script.

This is the "progressive disclosure" layer - SKILL.md is injected up front,
everything else is fetched only when the model asks for it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from act_agent.skills.registry import SkillRegistry

MAX_REFERENCE_BYTES = 60_000
MAX_OUTPUT_CHARS = 20_000
SCRIPT_TIMEOUT_SECONDS = 30.0


class ReadReferenceArgs(BaseModel):
    skill_name: str = Field(description="skill 名称，例如 act-mcp-combined-create")
    path: str = Field(description="references 下的相对路径，例如 tool-contract.md")


class RunScriptArgs(BaseModel):
    skill_name: str = Field(description="skill 名称")
    script: str = Field(description="scripts 下的相对路径，例如 validate_request.py")
    args: list[str] = Field(
        default_factory=list, description="命令行参数列表（逐项传，不要拼成字符串）"
    )


def build_skill_tools(registry: SkillRegistry) -> list:
    @tool("skill_read_reference", args_schema=ReadReferenceArgs)
    def skill_read_reference(skill_name: str, path: str) -> str:
        """读取某个 skill 的参考文档（references 目录）。skill 正文不够用时再读。"""
        skill = registry.get(skill_name)
        if skill is None:
            return f"错误：未找到 skill {skill_name}"
        resolved = skill.reference_path(path)
        if resolved is None:
            available = ", ".join(skill.references) or "（无）"
            return f"错误：{path} 不存在。可用文档：{available}"
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_REFERENCE_BYTES:
            text = text[:MAX_REFERENCE_BYTES] + "\n...[已截断]"
        return text

    @tool("skill_run_script", args_schema=RunScriptArgs)
    async def skill_run_script(skill_name: str, script: str, args: list[str] | None = None) -> str:
        """在隔离环境中执行 skill 自带的脚本（scripts 目录），返回退出码与输出。"""
        skill = registry.get(skill_name)
        if skill is None:
            return json.dumps({"error": f"未找到 skill {skill_name}"}, ensure_ascii=False)
        resolved = skill.script_path(script)
        if resolved is None:
            available = ", ".join(skill.scripts) or "（无）"
            return json.dumps(
                {"error": f"{script} 不存在", "available": available}, ensure_ascii=False
            )
        return await _execute(skill.path, resolved, args or [])

    return [skill_read_reference, skill_run_script]


async def _execute(skill_dir: Path, script: Path, args: list[str]) -> str:
    suffix = script.suffix.lower()
    if suffix == ".py":
        cmd = [sys.executable, "-I", str(script), *args]
    elif suffix in {".sh", ".bash"}:
        cmd = ["/bin/bash", str(script), *args]
    else:
        return json.dumps({"error": f"不允许执行的脚本类型: {suffix}"}, ensure_ascii=False)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return json.dumps({"error": f"启动失败: {exc}"}, ensure_ascii=False)

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=SCRIPT_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return json.dumps({"error": "脚本执行超时"}, ensure_ascii=False)

    payload = {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS],
        "stderr": stderr.decode("utf-8", "replace")[:4000],
    }
    return json.dumps(payload, ensure_ascii=False)
