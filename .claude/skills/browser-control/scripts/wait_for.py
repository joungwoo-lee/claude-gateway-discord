#!/usr/bin/env python3
from __future__ import annotations

import argparse
from playwright.sync_api import sync_playwright
from common import write_json, guard_before_and_after, PolicyError


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--selector")
    p.add_argument("--text")
    p.add_argument("--timeout-ms", type=int, default=10000)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    if not args.selector and not args.text:
        raise SystemExit("Provide --selector or --text")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            page = browser.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            guard_before_and_after(args.url, page.url, "read")
            if args.selector:
                page.wait_for_selector(args.selector, timeout=args.timeout_ms)
            if args.text:
                page.get_by_text(args.text).first.wait_for(timeout=args.timeout_ms)
            payload = {"ok": True, "url": page.url, "selector": args.selector, "text": args.text}
            browser.close()
    except PolicyError as e:
        payload = e.payload

    write_json(args.out, payload)
    print(payload)


if __name__ == "__main__":
    main()
