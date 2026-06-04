"""GitHub Personal Access Token 解析与脱敏。"""

from __future__ import annotations

from typing import Any, Dict, List


def normalize_github_tokens(config: Dict[str, Any]) -> List[str]:
    """从 config 合并单 Token 与多 Token 列表，去重并保持顺序。"""
    tokens: List[str] = []
    multi = config.get("Github_Personal_Tokens")
    if isinstance(multi, list):
        for item in multi:
            t = str(item or "").strip()
            if t:
                tokens.append(t)
    single = str(config.get("Github_Personal_Token", "")).strip()
    if single and single not in tokens:
        tokens.insert(0, single)
    seen: set[str] = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def parse_github_tokens_input(raw: str) -> List[str]:
    """解析后台输入：每行一个，或用逗号分隔。"""
    tokens: List[str] = []
    for line in str(raw or "").replace(",", "\n").splitlines():
        t = line.strip()
        if t and len(t) >= 10:
            tokens.append(t)
    seen: set[str] = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def mask_github_token(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"
