#!/usr/bin/env python3
from __future__ import annotations

import argparse
from playwright.sync_api import sync_playwright
from common import write_json, guard_before_and_after, PolicyError

JS = r'''
() => {
  const els = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[onclick]'));
  return els.slice(0, 300).map((el, i) => ({
    idx: i,
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.getAttribute('name'),
    type: el.getAttribute('type'),
    role: el.getAttribute('role'),
    text: (el.innerText || el.value || '').trim().slice(0, 120),
    testid: el.getAttribute('data-testid'),
  }));
}
'''


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--timeout-ms", type=int, default=30000)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            page = browser.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            guard_before_and_after(args.url, page.url, "read")
            items = page.evaluate(JS)
            payload = {"ok": True, "url": page.url, "count": len(items), "items": items}
            browser.close()
    except PolicyError as e:
        payload = e.payload

    write_json(args.out, payload)
    print({"ok": payload.get("ok"), "count": payload.get("count"), "reason": payload.get("reason")})


if __name__ == "__main__":
    main()
