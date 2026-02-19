#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse


class PolicyError(Exception):
    def __init__(self, payload: Dict[str, Any]):
        super().__init__(payload.get("reason", "policy_denied"))
        self.payload = payload


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: str, payload: Dict[str, Any]) -> None:
    p = Path(path)
    ensure_parent(p)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def load_policy() -> Dict[str, List[str]]:
    """
    Priority:
    1) browser-control/policy.yaml
    2) browser-control/policy.json

    policy.yaml is parsed as JSON-compatible YAML for zero dependency operation.
    """
    candidates = [_skill_dir() / "policy.yaml", _skill_dir() / "policy.json"]
    for p in candidates:
        if p.exists():
            txt = _load_text(p)
            if not txt:
                break
            try:
                obj = json.loads(txt)
            except json.JSONDecodeError as e:
                raise PolicyError(
                    {
                        "ok": False,
                        "decision": "deny",
                        "reason": "policy_parse_error",
                        "policy_file": str(p),
                        "error": str(e),
                    }
                )
            return {
                "write_allowed_hosts": [str(x).lower() for x in obj.get("write_allowed_hosts", [])],
                "read_blocked_hosts": [str(x).lower() for x in obj.get("read_blocked_hosts", [])],
            }
    raise PolicyError(
        {
            "ok": False,
            "decision": "deny",
            "reason": "policy_not_found",
            "expected_files": [str(c) for c in candidates],
        }
    )


def _normalize_pattern(host_pattern: str) -> str:
    p = host_pattern.strip().lower()
    if p.startswith("*."):
        p = p[2:]
    if p.startswith("."):
        p = p[1:]
    return p


def host_matches(host: str, host_pattern: str) -> bool:
    host = host.lower()
    p = _normalize_pattern(host_pattern)
    return host == p or host.endswith("." + p)


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def check_url(url: str, action_type: str, policy: Dict[str, List[str]] | None = None, stage: str = "request") -> None:
    policy = policy or load_policy()
    host = _hostname(url)
    if not host:
        raise PolicyError(
            {
                "ok": False,
                "decision": "deny",
                "reason": "invalid_url",
                "action_type": action_type,
                "url": url,
                "stage": stage,
            }
        )

    if action_type == "read":
        blocked = any(host_matches(host, p) for p in policy.get("read_blocked_hosts", []))
        if blocked:
            raise PolicyError(
                {
                    "ok": False,
                    "decision": "deny",
                    "reason": "read_blocked_host",
                    "action_type": action_type,
                    "url": url,
                    "hostname": host,
                    "stage": stage,
                }
            )
        return

    if action_type == "write":
        allowed = any(host_matches(host, p) for p in policy.get("write_allowed_hosts", []))
        if not allowed:
            raise PolicyError(
                {
                    "ok": False,
                    "decision": "deny",
                    "reason": "write_host_not_allowed",
                    "action_type": action_type,
                    "url": url,
                    "hostname": host,
                    "stage": stage,
                }
            )
        return

    raise PolicyError(
        {
            "ok": False,
            "decision": "deny",
            "reason": "unknown_action_type",
            "action_type": action_type,
            "url": url,
            "stage": stage,
        }
    )


def guard_before_and_after(initial_url: str, final_url: str, action_type: str) -> None:
    policy = load_policy()
    check_url(initial_url, action_type, policy=policy, stage="request")
    check_url(final_url, action_type, policy=policy, stage="final")
