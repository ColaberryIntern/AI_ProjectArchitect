"""Drive a Vimeo upload via Playwright.

Flow:
  1. Launch headed Chromium pointed at Vimeo login.
  2. WAIT FOR YOU TO LOG IN MANUALLY (handles 2FA, captcha, etc.)
     Detected by URL navigating away from the log_in page.
  3. Navigate to the upload page.
  4. Set the video file on the upload input.
  5. Wait for processing; extract the canonical share URL.

Writes step-by-step progress to vimeo_upload_status.jsonl so this script
can be monitored from outside while it runs in the background.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

HERE = Path(__file__).parent.resolve()
STATUS = HERE / "vimeo_upload_status.jsonl"
# Persist Chromium profile OUTSIDE OneDrive — OneDrive locks the ProcessSingleton
# file and prevents Chromium from launching. Also avoids syncing cookies to cloud.
USER_DATA = Path.home() / "AppData" / "Local" / "ai-project-architect" / "vimeo-profile"

# CLI: vimeo_upload.py [video_path] [title]
_default_video = HERE.parent / "assets" / "videos" / "all-animatic.mp4"
VIDEO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _default_video.resolve()
TITLE = sys.argv[2] if len(sys.argv) > 2 else VIDEO.stem.replace("_", " ").replace("-", " ")
DESCRIPTION = (
    "Internal training video for the AI Project Architect & Build Companion."
)


def log(msg: str, **kwargs) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    rec = {"t": time.time(), "ts": stamp, "msg": msg, **kwargs}
    with STATUS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def main() -> int:
    if not VIDEO.exists():
        log("video file not found", path=str(VIDEO), status="error")
        return 2

    log("starting", video=str(VIDEO), size_mb=round(VIDEO.stat().st_size / 1024 / 1024, 1))

    with sync_playwright() as p:
        log("launching Chromium (visible window will appear)")
        # Persistent context — saves Vimeo cookies across runs so you don't
        # have to log in every upload. Stored in a gitignored profile dir.
        context = p.chromium.launch_persistent_context(
            str(USER_DATA),
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            no_viewport=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.pages[0] if context.pages else context.new_page()

        # 1. Login — check if cookies already authenticate us; otherwise drive
        #    the user to log in manually.
        #
        #    Detection: blacklist the URLs Vimeo uses for unauthenticated
        #    flows. Anything else (/home, /feed, /{username}, /manage, etc.)
        #    counts as logged in. Whitelisting specific paths is too narrow
        #    — Vimeo drops users on plan-dependent landing pages after login.
        def _needs_login(url: str) -> bool:
            blockers = ("/log_in", "/login", "/join", "/signup",
                        "/verify", "/oauth", "/forgot")
            return any(b in url for b in blockers)

        log("opening Vimeo home to check session")
        page.goto("https://vimeo.com/home", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        current = page.url

        if _needs_login(current):
            log(f"not logged in (at {current}) — opening login page",
                status="awaiting_login")
            page.goto("https://vimeo.com/log_in", wait_until="domcontentloaded")
            log("waiting for you to log in (up to 10 minutes)")
            try:
                page.wait_for_url(
                    lambda url: not _needs_login(url),
                    timeout=600_000,
                )
            except PWTimeout:
                log("login timed out — closing", status="error")
                context.close()
                return 3
            log(f"logged in at {page.url}", status="logged_in", url=page.url)
        else:
            log(f"session restored — already logged in at {page.url}",
                status="logged_in_persisted", url=page.url)

        # Brief settle so any post-login redirects finish
        page.wait_for_load_state("networkidle", timeout=30_000)

        # 2. Navigate to upload. Try the modern manage path first; fall back to legacy.
        log("navigating to upload page")
        try:
            page.goto("https://vimeo.com/manage/videos", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        except PWTimeout:
            log("manage page slow; trying /upload directly")
            page.goto("https://vimeo.com/upload", wait_until="domcontentloaded")

        # Click "New video" / "Upload" if needed
        for label in ["New video", "Upload", "Upload video"]:
            try:
                btn = page.get_by_role("button", name=label, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=5000)
                    log(f"clicked '{label}'")
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        # Some flows pop a menu; pick the "Upload" item if shown
        for label in ["Upload", "Upload from device"]:
            try:
                item = page.get_by_role("menuitem", name=label, exact=False)
                if item.count() > 0:
                    item.first.click(timeout=3000)
                    log(f"clicked menu item '{label}'")
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        # 3. Find file input
        log("looking for file input")
        file_input = None
        for sel in [
            'input[type="file"][accept*="video"]',
            'input[type="file"]',
        ]:
            loc = page.locator(sel)
            if loc.count() > 0:
                file_input = loc.first
                log(f"found file input via {sel!r}")
                break

        if file_input is None:
            log(
                "could not find file input — pausing 5 minutes so you can navigate "
                "to the upload page manually; the script will still complete the upload "
                "once a file input is visible",
                status="awaiting_navigation",
            )
            deadline = time.time() + 300
            while time.time() < deadline:
                loc = page.locator('input[type="file"]')
                if loc.count() > 0:
                    file_input = loc.first
                    log("file input appeared")
                    break
                page.wait_for_timeout(2000)

        if file_input is None:
            log("file input never appeared — leaving browser open", status="error")
            time.sleep(120)
            context.close()
            return 4

        # 4. Set the file
        log("setting file on input", file=str(VIDEO))
        file_input.set_input_files(str(VIDEO))
        log("file set — upload should start", status="uploading")

        # 5. Wait for upload + processing. Two signals:
        #    a) URL navigates to /manage/videos/{id}/... or /video/{id}
        #    b) A "Save" / "Done" button becomes enabled
        # We give it up to 15 min.
        share_url = None
        try:
            page.wait_for_url(
                lambda url: bool(
                    re.search(r"/manage/videos/\d{6,}", url)
                    or re.search(r"vimeo\.com/\d{6,}", url)
                ),
                timeout=900_000,
            )
            log(f"redirected to {page.url}", status="processing", url=page.url)
        except PWTimeout:
            log("no URL change after 15 min — scraping page for the video URL")

        # Try to extract canonical share URL from the page
        candidates: list[str] = []
        candidates.append(page.url)

        try:
            for a in page.locator("a[href*='/manage/videos/']").all():
                href = a.get_attribute("href")
                if href:
                    candidates.append(href)
        except Exception:
            pass

        try:
            for inp in page.locator("input[value*='vimeo.com/']").all():
                v = inp.get_attribute("value")
                if v:
                    candidates.append(v)
        except Exception:
            pass

        for c in candidates:
            m = re.search(r"vimeo\.com/(\d{6,})", c) or re.search(r"/manage/videos/(\d{6,})", c)
            if m:
                share_url = f"https://vimeo.com/{m.group(1)}"
                break

        if share_url:
            log("share URL captured", status="complete", share_url=share_url)
            print("\n========================================")
            print(f"SHARE URL: {share_url}")
            print("========================================\n")
        else:
            log(
                "could not auto-extract URL — copy it from the browser; "
                "leaving window open for 3 minutes",
                status="needs_manual_grab",
                final_url=page.url,
            )

        # Keep the browser open briefly so you can verify
        time.sleep(180)
        context.close()

    return 0 if share_url else 5


if __name__ == "__main__":
    sys.exit(main())
