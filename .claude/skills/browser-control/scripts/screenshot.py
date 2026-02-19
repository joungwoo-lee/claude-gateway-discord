#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from common import write_json, guard_before_and_after, PolicyError


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--full-page", action="store_true")
    p.add_argument("--timeout-ms", type=int, default=30000)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    img = Path(args.image)
    img.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            page = browser.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            guard_before_and_after(args.url, page.url, "read")
            page.screenshot(path=str(img), full_page=args.full_page)
            payload = {"ok": True, "url": page.url, "image": str(img.resolve())}
            browser.close()
    except PolicyError as e:
        payload = e.payload

    write_json(args.out, payload)
    print(payload)


if __name__ == "__main__":
    main()
