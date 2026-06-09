"""Synthetic UI mockup rendering for the training animatic.

These are not screenshots of the real app. They are simplified
visual proxies — fake browser frames containing approximate
renderings of each product surface — so the viewer can see the
shape of what they're being told about.

Each mockup function takes a (draw, image) pair and a bounding box,
and renders into that box. The caller composes a slide around it.
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# --- palette (matches the slide theme) --------------------------------------

BG = "#0f172a"
PANEL = "#1e293b"
PANEL_LIGHT = "#334155"
BORDER = "#475569"
TEXT = "#f8fafc"
MUTED = "#94a3b8"
ACCENT = "#22d3ee"
ACCENT_DARK = "#0e7490"

# product-palette
GREEN_INSTALL = "#10b981"
GREEN_INSTALL_BORDER = "#34d399"
GREEN_INSTALL_DARK = "#047857"
RED = "#ef4444"
YELLOW = "#f59e0b"
BLUE = "#3b82f6"
PURPLE = "#a855f7"
PINK = "#ec4899"


# --- font helper ------------------------------------------------------------

def _font(candidates: list[str], size: int):
    for n in candidates:
        try:
            return ImageFont.truetype(n, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def font(size: int):
    return _font(["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"], size)


def font_bold(size: int):
    return _font(["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"], size)


def font_mono(size: int):
    return _font(["consola.ttf", "couri.ttf", "DejaVuSansMono.ttf"], size)


# --- primitives -------------------------------------------------------------

def browser_frame(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    url: str,
    title: str | None = None,
) -> tuple[int, int, int, int]:
    """Draw a fake browser window. Returns the inner content box."""
    x0, y0, x1, y1 = box
    # window shadow
    draw.rounded_rectangle((x0 + 6, y0 + 6, x1 + 6, y1 + 6), radius=14, fill="#020617")
    # window
    draw.rounded_rectangle((x0, y0, x1, y1), radius=14, fill="#0b1220", outline=BORDER, width=2)
    # title bar
    bar_h = 56
    draw.rounded_rectangle((x0, y0, x1, y0 + bar_h), radius=14, fill="#1e293b")
    # square off the bottom of the title bar so it doesn't have rounded corners on the seam
    draw.rectangle((x0, y0 + bar_h - 14, x1, y0 + bar_h), fill="#1e293b")

    # traffic lights
    cx, cy, r = x0 + 22, y0 + 28, 7
    for color, dx in [(RED, 0), (YELLOW, 22), (GREEN_INSTALL, 44)]:
        draw.ellipse((cx + dx - r, cy - r, cx + dx + r, cy + r), fill=color)

    # URL bar
    url_x0, url_y0 = x0 + 110, y0 + 12
    url_x1, url_y1 = x1 - 110, y0 + bar_h - 12
    draw.rounded_rectangle((url_x0, url_y0, url_x1, url_y1), radius=8, fill="#0f172a", outline=BORDER, width=1)
    # padlock (drawn)
    lx, ly = url_x0 + 12, url_y0 + 8
    draw.rectangle((lx, ly + 8, lx + 14, ly + 20), fill=MUTED)
    draw.arc((lx + 1, ly, lx + 13, ly + 14), start=180, end=360, fill=MUTED, width=2)
    # url text
    draw.text((url_x0 + 42, url_y0 + 6), url, font=font(20), fill=TEXT)

    # tab title
    if title:
        tab_w = 220
        tab_x0 = x0 + 12
        tab_y0 = y0 - 4
        draw.rounded_rectangle(
            (tab_x0, tab_y0, tab_x0 + tab_w, tab_y0 + 32),
            radius=8,
            fill="#1e293b",
            outline=BORDER,
            width=1,
        )
        draw.text((tab_x0 + 14, tab_y0 + 6), title, font=font(16), fill=MUTED)

    return (x0 + 2, y0 + bar_h + 2, x1 - 2, y1 - 2)


def pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    bg: str,
    fg: str = TEXT,
    pad_x: int = 16,
    pad_y: int = 6,
    f=None,
) -> tuple[int, int]:
    f = f or font_bold(18)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = tw + 2 * pad_x, th + 2 * pad_y + 6
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=f, fill=fg)
    return (x + w, y + h)


def button(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    bg: str = GREEN_INSTALL,
    fg: str = TEXT,
    f=None,
):
    f = f or font_bold(28)
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=10, fill=bg, outline=GREEN_INSTALL_BORDER, width=2)
    bbox = draw.textbbox((0, 0), label, font=f)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((x0 + (x1 - x0 - tw) // 2, y0 + (y1 - y0 - th) // 2 - 4), label, font=f, fill=fg)


def icon_square(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    letter: str,
    bg: str,
    fg: str = TEXT,
) -> None:
    """A simple colored rounded square with a centered letter — a stand-in for product icons."""
    draw.rounded_rectangle((x, y, x + size, y + size), radius=max(6, size // 6), fill=bg)
    f = font_bold(int(size * 0.55))
    bbox = draw.textbbox((0, 0), letter, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x + (size - tw) // 2, y + (size - th) // 2 - 4), letter, font=f, fill=fg)


def icon_download(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str = TEXT) -> None:
    """A simple down-arrow into a tray icon, ~ size px square."""
    # arrow shaft
    cx = x + size // 2
    draw.rectangle((cx - size // 14, y + size // 8, cx + size // 14, y + size // 2 + 4), fill=color)
    # arrow head (triangle)
    draw.polygon(
        [(cx - size // 4, y + size // 2 - 4),
         (cx + size // 4, y + size // 2 - 4),
         (cx, y + size * 3 // 4)],
        fill=color,
    )
    # tray (bottom bar)
    draw.rectangle((x + size // 6, y + size - size // 6, x + size - size // 6, y + size - size // 12), fill=color)


def icon_search(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str = MUTED) -> None:
    """A simple magnifying-glass icon."""
    r = size // 3
    cx, cy = x + r + 2, y + r + 2
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=3)
    draw.line([(cx + int(r * 0.7), cy + int(r * 0.7)),
                (x + size - 2, y + size - 2)], fill=color, width=3)


def checkbox(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, checked: bool = True):
    draw.rounded_rectangle((x, y, x + size, y + size), radius=5, fill=BG if not checked else GREEN_INSTALL,
                           outline=GREEN_INSTALL_BORDER, width=2)
    if checked:
        # checkmark
        draw.line([(x + size * 0.22, y + size * 0.52),
                   (x + size * 0.45, y + size * 0.74),
                   (x + size * 0.80, y + size * 0.28)], fill=TEXT, width=4)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), trial, font=f)
        if bbox[2] - bbox[0] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def text_block(draw, text, xy, f, color, max_width, line_spacing=8) -> int:
    x, y = xy
    total = 0
    for line in wrap(draw, text, f, max_width):
        draw.text((x, y + total), line, font=f, fill=color)
        bbox = draw.textbbox((0, 0), line, font=f)
        total += (bbox[3] - bbox[1]) + line_spacing
    return total


# --- mockups ----------------------------------------------------------------

def mockup_title_card(draw, box, ctx: dict):
    """Plain title card — just text. For intro/outro scenes."""
    x0, y0, x1, y1 = box
    title = ctx.get("title", "")
    subtitle = ctx.get("subtitle", "")
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2

    f1 = font_bold(72)
    bbox = draw.textbbox((0, 0), title, font=f1)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, cy - 60), title, font=f1, fill=TEXT)

    if subtitle:
        f2 = font(36)
        bbox = draw.textbbox((0, 0), subtitle, font=f2)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, cy + 30), subtitle, font=f2, fill=MUTED)


def mockup_my_day_queue(draw, box, ctx: dict):
    """The /my-day/ task queue page."""
    inner = browser_frame(draw, box, "advisor.colaberry.ai/my-day/", "My Day")
    x0, y0, x1, y1 = inner

    # page header
    draw.text((x0 + 30, y0 + 20), "My Day", font=font_bold(40), fill=TEXT)
    draw.text((x0 + 30, y0 + 70), "What needs you, right now.", font=font(20), fill=MUTED)

    # sync button (top right)
    sync_w = 140
    sync_x = x1 - 30 - sync_w
    draw.rounded_rectangle((sync_x, y0 + 25, sync_x + sync_w, y0 + 70), radius=8,
                            fill=ACCENT_DARK, outline=ACCENT, width=1)
    # circular arrow icon
    arc_cx, arc_cy = sync_x + 24, y0 + 47
    draw.arc((arc_cx - 11, arc_cy - 11, arc_cx + 11, arc_cy + 11),
              start=30, end=320, fill=TEXT, width=3)
    # arrowhead
    draw.polygon([(arc_cx + 11, arc_cy - 8),
                   (arc_cx + 16, arc_cy + 2),
                   (arc_cx + 5, arc_cy - 2)], fill=TEXT)
    draw.text((sync_x + 50, y0 + 35), "Sync", font=font_bold(22), fill=TEXT)

    # last sync
    draw.text((sync_x - 220, y0 + 38), "Last sync: just now", font=font(16), fill=MUTED)

    # tier groups
    y = y0 + 130
    tiers = ctx.get("tiers", [
        ("ASSIGNED TO YOU", [
            ("Review draft for ACME pricing model", "Due today", RED, "human_required"),
            ("Set up Playwright tests for checkout flow", "Tomorrow", YELLOW, "human_required"),
            ("Reply to Sarah re: onboarding doc", "—", BLUE, "waiting_dependency"),
        ]),
        ("DUE SOON", [
            ("Weekly status update for Project Atlas", "Tomorrow", YELLOW, "human_required"),
            ("Review PR #482: auth middleware refactor", "Thu", BLUE, "human_required"),
        ]),
        ("UNASSIGNED IN YOUR PROJECTS", [
            ("Investigate Stripe webhook 5xx", "—", MUTED, "unscored"),
        ]),
    ])

    highlight_idx = ctx.get("highlight", None)  # (tier_idx, row_idx) or None
    dismissed_idx = ctx.get("dismissed", None)
    show_filter_sidebar = ctx.get("show_filter", False)

    content_x0 = x0 + 30
    content_x1 = x1 - 30
    if show_filter_sidebar:
        # sidebar on left
        sb_w = 220
        draw.rounded_rectangle((content_x0, y, content_x0 + sb_w, y + 380), radius=10,
                                fill=PANEL, outline=BORDER, width=1)
        draw.text((content_x0 + 18, y + 18), "FILTERS", font=font_bold(18), fill=ACCENT)
        filters = [("Assigned", True), ("Due Soon", False), ("Unassigned", False),
                   ("", None), ("human_required", True), ("waiting_dependency", False),
                   ("unscored", False)]
        fy = y + 56
        for label, on in filters:
            if not label:
                draw.line([(content_x0 + 18, fy), (content_x0 + sb_w - 18, fy)], fill=BORDER, width=1)
                fy += 16
                continue
            checkbox(draw, content_x0 + 18, fy, 18, bool(on))
            draw.text((content_x0 + 48, fy - 2), label, font=font(18),
                       fill=TEXT if on else MUTED)
            fy += 38
        content_x0 += sb_w + 20

    for ti, (tier_label, tasks) in enumerate(tiers):
        # tier header
        draw.text((content_x0, y), tier_label, font=font_bold(20), fill=ACCENT)
        y += 36
        for ri, (title, due, due_color, category) in enumerate(tasks):
            is_highlight = highlight_idx == (ti, ri)
            is_dismissed = dismissed_idx == (ti, ri)
            if is_dismissed:
                # dimmed strikethrough
                continue

            row_y0, row_y1 = y, y + 60
            row_bg = PANEL_LIGHT if is_highlight else PANEL
            border_color = ACCENT if is_highlight else BORDER
            draw.rounded_rectangle((content_x0, row_y0, content_x1, row_y1),
                                    radius=8, fill=row_bg, outline=border_color, width=1 + (2 if is_highlight else 0))

            # urgency dot
            draw.ellipse((content_x0 + 16, row_y0 + 22, content_x0 + 32, row_y0 + 38), fill=due_color)
            # title
            draw.text((content_x0 + 50, row_y0 + 14), title, font=font(20), fill=TEXT)
            # category pill
            cat_color = {"human_required": "#7c3aed", "waiting_dependency": "#0891b2", "unscored": "#475569"}[category]
            pill(draw, content_x0 + 50, row_y0 + 38, category, cat_color, f=font_bold(13), pad_x=8, pad_y=2)
            # due
            draw.text((content_x1 - 90, row_y0 + 20), due, font=font_bold(18), fill=due_color)

            y += 70

        y += 14


def mockup_library_home(draw, box, ctx: dict):
    """The /library/ category grid."""
    inner = browser_frame(draw, box, "advisor.colaberry.ai/library/", "Library")
    x0, y0, x1, y1 = inner

    # page header
    draw.text((x0 + 30, y0 + 20), "Library", font=font_bold(40), fill=TEXT)
    draw.text((x0 + 30, y0 + 70), "Reusable building blocks for your work.", font=font(20), fill=MUTED)

    # search bar
    sb_y0, sb_y1 = y0 + 110, y0 + 156
    draw.rounded_rectangle((x0 + 30, sb_y0, x1 - 30, sb_y1), radius=10, fill=PANEL, outline=BORDER, width=1)
    icon_search(draw, x0 + 50, sb_y0 + 12, 26, MUTED)
    search_q = ctx.get("search", "")
    if search_q:
        draw.text((x0 + 90, sb_y0 + 12), search_q, font=font(20), fill=TEXT)
        # blinking cursor
        bbox = draw.textbbox((0, 0), search_q, font=font(20))
        tw = bbox[2] - bbox[0]
        draw.line([(x0 + 90 + tw + 4, sb_y0 + 14), (x0 + 90 + tw + 4, sb_y0 + 34)], fill=ACCENT, width=2)
    else:
        draw.text((x0 + 90, sb_y0 + 12), "Search across all categories…", font=font(20), fill=MUTED)

    # categories
    categories = [
        ("Skills", "Focused capabilities", "S", PURPLE),
        ("Agents", "Purpose-built subagents", "A", BLUE),
        ("Prompts", "Vetted templates", "P", PINK),
        ("MCP Servers", "External tools & data", "M", ACCENT),
        ("Use Cases", "Worked examples", "U", YELLOW),
        ("Capabilities", "Cross-cutting features", "C", GREEN_INSTALL),
    ]

    # 3 cols x 2 rows
    grid_y0 = sb_y1 + 30
    available_w = (x1 - x0) - 60
    gap = 20
    card_w = (available_w - 2 * gap) // 3
    card_h = 130
    for i, (name, sub, icon, color) in enumerate(categories):
        col = i % 3
        row = i // 3
        cx0 = x0 + 30 + col * (card_w + gap)
        cy0 = grid_y0 + row * (card_h + gap)
        cx1 = cx0 + card_w
        cy1 = cy0 + card_h
        draw.rounded_rectangle((cx0, cy0, cx1, cy1), radius=12, fill=PANEL, outline=BORDER, width=1)
        # accent strip on top
        draw.rounded_rectangle((cx0, cy0, cx1, cy0 + 6), radius=12, fill=color)
        draw.rectangle((cx0, cy0 + 3, cx1, cy0 + 6), fill=color)
        # icon + text
        icon_square(draw, cx0 + 20, cy0 + 26, 56, icon, color)
        draw.text((cx0 + 92, cy0 + 30), name, font=font_bold(26), fill=TEXT)
        draw.text((cx0 + 92, cy0 + 64), sub, font=font(18), fill=MUTED)


def mockup_library_detail(draw, box, ctx: dict):
    """A Library asset detail page with the green Install panel.

    ctx options:
      - asset_name, asset_kind, asset_desc
      - install_panel_state: "default" | "highlighted" | "missing" | "success"
      - pr_url (used when state=success)
    """
    inner = browser_frame(draw, box, "advisor.colaberry.ai/library/asset/skills/verify",
                           "verify · Skills · Library")
    x0, y0, x1, y1 = inner

    asset_name = ctx.get("asset_name", "verify")
    asset_kind = ctx.get("asset_kind", "Skill")
    asset_desc = ctx.get("asset_desc",
                          "Verify a code change actually does what it's supposed to "
                          "by running the app and observing behavior.")
    state = ctx.get("install_panel_state", "default")

    # breadcrumb
    draw.text((x0 + 30, y0 + 16), f"Library  /  Skills  /  {asset_name}",
               font=font(16), fill=MUTED)

    # asset title row
    title_y = y0 + 46
    pill(draw, x0 + 30, title_y + 4, asset_kind.upper(), ACCENT_DARK, f=font_bold(14), pad_x=10, pad_y=3)
    draw.text((x0 + 130, title_y), asset_name, font=font_bold(40), fill=TEXT)

    # rating
    rating_x = x1 - 250
    for i in range(5):
        color = YELLOW if i < 4 else BORDER
        draw.text((rating_x + i * 22, title_y + 8), "★", font=font(28), fill=color)
    draw.text((rating_x + 120, title_y + 16), "4.6  (12)", font=font(18), fill=MUTED)

    # description
    desc_y = title_y + 70
    h = text_block(draw, asset_desc, (x0 + 30, desc_y), font(22), TEXT, x1 - x0 - 60, line_spacing=10)
    desc_y += h + 14

    # how-to-use header
    draw.text((x0 + 30, desc_y), "HOW TO USE", font=font_bold(18), fill=ACCENT)
    desc_y += 30
    draw.rounded_rectangle((x0 + 30, desc_y, x1 - 30, desc_y + 60), radius=8,
                            fill="#0b1220", outline=BORDER, width=1)
    draw.text((x0 + 45, desc_y + 18), "/verify  — runs the app and confirms the change works",
               font=font_mono(20), fill=ACCENT)
    desc_y += 80

    # install panel
    panel_y0 = desc_y
    panel_y1 = panel_y0 + 200
    if state == "missing":
        # show a muted note where the panel would be
        draw.rounded_rectangle((x0 + 30, panel_y0, x1 - 30, panel_y1),
                                radius=12, fill=PANEL, outline=BORDER, width=1)
        # info dot
        draw.ellipse((x0 + 50, panel_y0 + 32, x0 + 78, panel_y0 + 60), outline=MUTED, width=2)
        draw.text((x0 + 60, panel_y0 + 32), "i", font=font_bold(22), fill=MUTED)
        draw.text((x0 + 96, panel_y0 + 30), "Live-in-MCP asset",
                   font=font_bold(24), fill=MUTED)
        draw.text((x0 + 50, panel_y0 + 78),
                   "This asset is already available via your Claude MCP.",
                   font=font(20), fill=MUTED)
        draw.text((x0 + 50, panel_y0 + 110),
                   "No install needed — use it directly from Claude.",
                   font=font(20), fill=MUTED)
        return

    # the green box
    border = "#34d399" if state == "highlighted" else GREEN_INSTALL_BORDER
    bw = 4 if state == "highlighted" else 2
    draw.rounded_rectangle((x0 + 30, panel_y0, x1 - 30, panel_y1),
                            radius=12, fill="#064e3b", outline=border, width=bw)
    # download icon
    icon_download(draw, x0 + 50, panel_y0 + 22, 30, TEXT)
    draw.text((x0 + 92, panel_y0 + 22), "Install to your workspace",
               font=font_bold(26), fill=TEXT)
    draw.text((x0 + 50, panel_y0 + 62), "Opens a pull request in your workspace_repo with this asset.",
               font=font(18), fill="#d1fae5")

    # subscribe checkbox row
    cb_y = panel_y0 + 110
    checkbox(draw, x0 + 50, cb_y, 22, True)
    draw.text((x0 + 84, cb_y - 4), "Subscribe to updates (auto-PR when this asset is bumped)",
               font=font(18), fill=TEXT)

    if state == "success":
        # success — green check + PR link
        cy = panel_y0 + 158
        draw.ellipse((x0 + 50, cy, x0 + 78, cy + 28), fill="#a7f3d0")
        draw.line([(x0 + 56, cy + 14), (x0 + 62, cy + 20), (x0 + 72, cy + 8)],
                    fill="#064e3b", width=4)
        draw.text((x0 + 92, cy + 2), "Installed!  PR opened: github.com/you/workspace/pull/127",
                   font=font_bold(20), fill="#a7f3d0")
    else:
        # button (bottom-right of panel)
        btn_w, btn_h = 200, 50
        button(draw, (x1 - 30 - 30 - btn_w, panel_y0 + 130, x1 - 30 - 30, panel_y0 + 130 + btn_h),
                "Install", bg=GREEN_INSTALL)


def mockup_bc_todo_attribution(draw, box, ctx: dict):
    """A Basecamp todo with a 'via your Claude Code' comment."""
    inner = browser_frame(draw, box, "basecamp.com/projects/atlas/todos/4821",
                           "Atlas · Basecamp")
    x0, y0, x1, y1 = inner

    # BC-style header
    draw.rectangle((x0, y0, x1, y0 + 50), fill="#f1efe6")  # BC's cream color band
    draw.text((x0 + 30, y0 + 12), "Project Atlas", font=font_bold(22), fill="#3d3d3d")
    draw.text((x0 + 30, y0 + 38), "To-dos", font=font(16), fill="#7a7a7a")

    # task header
    task_y = y0 + 90
    # checkbox
    draw.rounded_rectangle((x0 + 30, task_y, x0 + 58, task_y + 28), radius=4,
                            fill="#0b1220", outline="#6ee7b7", width=2)
    draw.line([(x0 + 36, task_y + 14), (x0 + 44, task_y + 22), (x0 + 52, task_y + 8)],
                fill="#6ee7b7", width=3)
    draw.text((x0 + 78, task_y - 2), "Review draft for ACME pricing model",
               font=font_bold(28), fill=TEXT)
    draw.text((x0 + 78, task_y + 36), "Assigned to Ali  ·  Due today",
               font=font(18), fill=MUTED)

    # comments header
    comments_y = task_y + 90
    draw.line([(x0 + 30, comments_y), (x1 - 30, comments_y)], fill=BORDER, width=1)
    draw.text((x0 + 30, comments_y + 12), "Comments", font=font_bold(18), fill=ACCENT)

    # comment 1 (human)
    c1_y = comments_y + 50
    draw.ellipse((x0 + 30, c1_y, x0 + 78, c1_y + 48), fill=PURPLE)
    draw.text((x0 + 46, c1_y + 10), "S", font=font_bold(28), fill=TEXT)
    draw.text((x0 + 92, c1_y), "Sarah K.", font=font_bold(20), fill=TEXT)
    draw.text((x0 + 92, c1_y + 26), "Can you confirm the discount tiers before EOD?",
               font=font(18), fill=TEXT)

    # comment 2 (via Claude Code)
    c2_y = c1_y + 90
    draw.ellipse((x0 + 30, c2_y, x0 + 78, c2_y + 48), fill=BLUE)
    draw.text((x0 + 48, c2_y + 10), "A", font=font_bold(28), fill=TEXT)
    draw.text((x0 + 92, c2_y), "Ali M.", font=font_bold(20), fill=TEXT)
    # the via tag
    pill(draw, x0 + 178, c2_y + 4, "via Ali's Claude Code", ACCENT_DARK,
          f=font_bold(14), pad_x=10, pad_y=3)
    draw.text((x0 + 92, c2_y + 38),
               "Confirmed with finance. Tiers locked: 10% / 20% / 30%.",
               font=font(18), fill=TEXT)
    draw.text((x0 + 92, c2_y + 64), "Closing this todo and opening follow-up #4823.",
               font=font(18), fill=TEXT)

    # highlight box pointing at the via tag
    box_x0, box_y0 = x0 + 170, c2_y - 6
    box_x1, box_y1 = x0 + 410, c2_y + 30
    draw.rounded_rectangle((box_x0, box_y0, box_x1, box_y1), radius=8,
                            outline=ACCENT, width=3)


def mockup_problem_diagram(draw, box, ctx: dict):
    """Three sources (Gmail, Basecamp, Library) all pulling at one user."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2

    # the user in the middle
    u_r = 60
    draw.ellipse((cx - u_r, cy - u_r, cx + u_r, cy + u_r), fill=PANEL, outline=ACCENT, width=4)
    # simple "person" silhouette
    draw.ellipse((cx - 22, cy - 36, cx + 22, cy + 8), fill=TEXT)  # head
    draw.rounded_rectangle((cx - 38, cy + 6, cx + 38, cy + 50), radius=24, fill=TEXT)  # body
    draw.text((cx - 30, cy + 80), "You", font=font_bold(28), fill=TEXT)

    # three sources placed in an arc around the top
    sources = [
        ("Gmail", "Customer emails", "@", RED, -1.0, -0.6),
        ("Basecamp", "Project tasks", "BC", "#a3e635", 0.0, -1.0),
        ("Library", "Skills, agents, prompts", "L", BLUE, 1.0, -0.6),
    ]
    radius_x = 380
    radius_y = 260
    for name, sub, icon, color, dx, dy in sources:
        sx = cx + int(dx * radius_x)
        sy = cy + int(dy * radius_y)
        # card
        card_w, card_h = 280, 110
        cx0 = sx - card_w // 2
        cy0 = sy - card_h // 2
        draw.rounded_rectangle((cx0, cy0, cx0 + card_w, cy0 + card_h), radius=12,
                                fill=PANEL, outline=color, width=3)
        icon_square(draw, cx0 + 20, cy0 + 22, 66, icon, color)
        draw.text((cx0 + 102, cy0 + 26), name, font=font_bold(26), fill=TEXT)
        draw.text((cx0 + 102, cy0 + 62), sub, font=font(16), fill=MUTED)
        # arrow toward the user with arrowhead
        import math
        sx2, sy2 = sx, cy0 + card_h
        ex, ey = cx + int(-dx * (u_r + 8)), cy - 30
        draw.line([(sx2, sy2), (ex, ey)], fill=color, width=3)
        # arrowhead
        ang = math.atan2(ey - sy2, ex - sx2)
        ah = 16
        ax1 = ex - ah * math.cos(ang - math.pi / 6)
        ay1 = ey - ah * math.sin(ang - math.pi / 6)
        ax2 = ex - ah * math.cos(ang + math.pi / 6)
        ay2 = ey - ah * math.sin(ang + math.pi / 6)
        draw.polygon([(ex, ey), (ax1, ay1), (ax2, ay2)], fill=color)


def mockup_email_flow_diagram(draw, box, ctx: dict):
    """Gmail/BC → BC todo + Drive → My Day queue."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0

    # 3-stage flow
    # stage 1 (left): sources
    # stage 2 (middle): BC todo + Drive
    # stage 3 (right): My Day queue
    col_w = w // 3 - 40
    col1_x = x0 + 20
    col2_x = x0 + w // 3 + 20
    col3_x = x0 + 2 * w // 3 + 20

    def draw_node(x, y, w, h, title, sub, color, letter):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=PANEL,
                                outline=color, width=3)
        icon_square(draw, x + 14, y + (h - 50) // 2, 50, letter, color)
        draw.text((x + 78, y + 16), title, font=font_bold(22), fill=TEXT)
        draw.text((x + 78, y + 48), sub, font=font(15), fill=MUTED)

    # column 1
    draw_node(col1_x, y0 + 50, col_w, 80, "Gmail", "Customer emails", RED, "@")
    draw_node(col1_x, y0 + 170, col_w, 80, "Basecamp", "Team tasks", "#a3e635", "BC")

    # column 2
    draw_node(col2_x, y0 + 70, col_w, 90, "BC todo created", "in the right project", "#a3e635", "✓")
    draw_node(col2_x, y0 + 200, col_w, 90, "Attachments staged", "to Google Drive", BLUE, "D")

    # column 3
    draw_node(col3_x, y0 + 130, col_w, 110, "My Day queue", "ranked by urgency", ACCENT, "MD")

    # arrows
    def arrow(x1, y1, x2, y2, color):
        draw.line([(x1, y1), (x2, y2)], fill=color, width=3)
        # arrowhead
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        ah = 12
        ax1 = x2 - ah * math.cos(ang - math.pi / 6)
        ay1 = y2 - ah * math.sin(ang - math.pi / 6)
        ax2 = x2 - ah * math.cos(ang + math.pi / 6)
        ay2 = y2 - ah * math.sin(ang + math.pi / 6)
        draw.polygon([(x2, y2), (ax1, ay1), (ax2, ay2)], fill=color)

    arrow(col1_x + col_w, y0 + 90, col2_x, y0 + 110, RED)
    arrow(col1_x + col_w, y0 + 210, col2_x, y0 + 110, "#a3e635")
    arrow(col2_x + col_w // 2, y0 + 160, col2_x + col_w // 2, y0 + 200, BLUE)
    arrow(col2_x + col_w, y0 + 110, col3_x, y0 + 180, ACCENT)
    arrow(col2_x + col_w, y0 + 240, col3_x, y0 + 200, ACCENT)


def mockup_bc_oauth(draw, box, ctx: dict):
    """The Basecamp OAuth authorization screen."""
    inner = browser_frame(draw, box, "launchpad.37signals.com/authorization/new",
                           "Authorize · Basecamp")
    x0, y0, x1, y1 = inner

    # BC cream banner
    draw.rectangle((x0, y0, x1, y0 + 90), fill="#f1efe6")
    draw.text((x0 + 30, y0 + 24), "BASECAMP", font=font_bold(36), fill="#3d3d3d")
    draw.text((x0 + 30, y0 + 64), "37signals · Authorize app", font=font(16), fill="#7a7a7a")

    # central card
    card_x0 = x0 + 120
    card_x1 = x1 - 120
    card_y0 = y0 + 130
    card_y1 = y1 - 80
    draw.rounded_rectangle((card_x0, card_y0, card_x1, card_y1), radius=14,
                            fill=PANEL, outline=BORDER, width=1)

    draw.text((card_x0 + 40, card_y0 + 30), "advisor.colaberry.ai", font=font_bold(28), fill=TEXT)
    draw.text((card_x0 + 40, card_y0 + 70), "would like to access your Basecamp account",
               font=font(22), fill=MUTED)

    # permission list
    perms = [
        ("Read", "your projects, todos, and comments"),
        ("Post", "comments and updates on your behalf"),
        ("Read", "attachments and project files"),
    ]
    py = card_y0 + 130
    for verb, what in perms:
        draw.ellipse((card_x0 + 40, py + 6, card_x0 + 60, py + 26), fill=ACCENT_DARK)
        draw.text((card_x0 + 45, py + 4), "✓", font=font_bold(20), fill=TEXT)
        draw.text((card_x0 + 80, py), verb, font=font_bold(20), fill=ACCENT)
        draw.text((card_x0 + 80 + 90, py), what, font=font(20), fill=TEXT)
        py += 44

    # buttons
    btn_y0 = card_y1 - 80
    btn_y1 = card_y1 - 30
    # deny
    draw.rounded_rectangle((card_x0 + 40, btn_y0, card_x0 + 200, btn_y1), radius=8,
                            fill=PANEL_LIGHT, outline=BORDER, width=1)
    draw.text((card_x0 + 90, btn_y0 + 12), "Deny", font=font_bold(22), fill=TEXT)
    # allow
    button(draw, (card_x1 - 220, btn_y0, card_x1 - 40, btn_y1), "Allow access", bg=GREEN_INSTALL)


# --- dispatch ---------------------------------------------------------------

MOCKUPS = {
    "title_card": mockup_title_card,
    "my_day_queue": mockup_my_day_queue,
    "library_home": mockup_library_home,
    "library_detail": mockup_library_detail,
    "bc_todo_attribution": mockup_bc_todo_attribution,
    "problem_diagram": mockup_problem_diagram,
    "email_flow_diagram": mockup_email_flow_diagram,
    "bc_oauth": mockup_bc_oauth,
}


def render_mockup(name: str, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], ctx: dict | None = None):
    """Dispatch to the named mockup function. Falls back to title_card if not found."""
    fn = MOCKUPS.get(name, mockup_title_card)
    fn(draw, box, ctx or {})
