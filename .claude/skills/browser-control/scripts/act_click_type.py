#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from playwright.sync_api import sync_playwright
from common import write_json, guard_before_and_after, PolicyError


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--actions", required=True, help="JSON array")
    p.add_argument("--timeout-ms", type=int, default=15000)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    actions = json.loads(args.actions)
    logs = []

    # Conservative policy: action execution is write-sensitive.
    action_type = "write"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            page = browser.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            guard_before_and_after(args.url, page.url, action_type)

            for i, a in enumerate(actions):
                t = a.get("type")
                sel = a.get("selector")
                try:
                    if t == "click":
                        page.locator(sel).first.click(timeout=args.timeout_ms)
                    elif t == "type":
                        page.locator(sel).first.fill(a.get("text", ""), timeout=args.timeout_ms)
                    elif t == "press":
                        page.locator(sel).first.press(a.get("key", "Enter"), timeout=args.timeout_ms)
                    else:
                        raise ValueError(f"unsupported action type: {t}")
                    logs.append({"index": i, "ok": True, "type": t, "selector": sel})
                except Exception as e:
                    logs.append({"index": i, "ok": False, "type": t, "selector": sel, "error": str(e)})
                    payload = {"ok": False, "url": page.url, "failed_index": i, "logs": logs}
                    browser.close()
                    write_json(args.out, payload)
                    print(payload)
                    raise SystemExit(2)

            guard_before_and_after(args.url, page.url, action_type)
            payload = {"ok": True, "url": page.url, "logs": logs}
            browser.close()
    except PolicyError as e:
        payload = e.payload

    write_json(args.out, payload)
    print(payload)


if __name__ == "__main__":
    main()
