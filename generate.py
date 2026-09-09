"""Generate the daily HN digest and its archive/weekly views.

Weekly rendering is deliberately manifest-only: it never downloads or copies images.
"""
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

BASE = "/hn-daily-digest"
HISTORY_DIR = "history"
SCREENSHOTS_DIR = "screenshots"
WEEKS_DIR = "weeks"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HNDigest/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def esc(value):
    value = str(value or "")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def parse_history_date(day):
    try:
        return datetime.strptime(day, "%d-%m-%Y").date()
    except (TypeError, ValueError):
        return None


def iso_week_key(day):
    """Return the ISO year/week key for a DD-MM-YYYY archive day."""
    date = parse_history_date(day)
    if not date:
        raise ValueError("invalid archive day: %s" % day)
    year, week, _ = date.isocalendar()
    return f"{year}-W{week:02d}"


def week_label(key):
    year, week = key.split("-W")
    monday = datetime.fromisocalendar(int(year), int(week), 1).date()
    return f"Week {int(week)} began {monday.day} {monday.strftime('%b')} {monday.year}"


def archive_days():
    if not os.path.isdir(HISTORY_DIR) or os.path.islink(HISTORY_DIR):
        return []
    return sorted((name for name in os.listdir(HISTORY_DIR)
                   if os.path.isdir(os.path.join(HISTORY_DIR, name))
                   and not os.path.islink(os.path.join(HISTORY_DIR, name))
                   and parse_history_date(name)),
                  key=parse_history_date, reverse=True)


def story_from_item(item, rank, screenshot_available):
    return {
        "id": item["id"], "title": item.get("title", "Untitled"),
        "url": item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}",
        "score": item.get("score", 0), "comments": item.get("descendants", 0),
        "author": item.get("by", "anon"), "time": item.get("time", 0),
        "screenshot_rank": rank if screenshot_available else None,
        "screenshot_path": f"screenshots/{rank}.jpg" if screenshot_available else None,
        "screenshot_available": bool(screenshot_available),
    }


class LegacyCardParser(HTMLParser):
    """Extract enough structured data from the old generated archive cards to backfill manifests."""
    def __init__(self):
        super().__init__()
        self.cards, self.card, self.depth, self.capture, self.buffer = [], None, 0, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "div" and "card" in classes and self.card is None:
            self.card, self.depth = {"title": "Untitled", "score": 0, "comments": 0, "author": "anon", "time": 0}, 1
            return
        if self.card is None:
            return
        if tag == "div": self.depth += 1
        if tag == "span" and "rank" in classes:
            self.capture, self.buffer = "rank", []
        elif tag == "a" and "title" in classes:
            self.card["url"] = attrs.get("href", "")
            self.capture, self.buffer = "title", []
        elif tag == "a" and "discuss" in classes:
            href = attrs.get("href", "")
            if "id=" in href:
                try: self.card["id"] = int(href.split("id=")[-1].split("&")[0])
                except ValueError: pass
        elif tag == "img" and attrs.get("src", "").startswith("screenshots/"):
            self.card["screenshot_path"] = attrs["src"]
            try: self.card["screenshot_rank"] = int(attrs["src"].rsplit("/", 1)[1].split(".")[0])
            except ValueError: pass
        elif tag == "div" and "meta" in classes:
            self.capture, self.buffer = "meta", []

    def handle_data(self, data):
        if self.card is not None and self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if self.card is None: return
        if tag == "span" and self.capture == "rank":
            try: self.card["screenshot_rank"] = int("".join(self.buffer).strip().lstrip("#"))
            except ValueError: pass
            self.capture = None
        elif tag == "a" and self.capture == "title":
            self.card["title"] = "".join(self.buffer).strip(); self.capture = None
        elif tag == "div" and self.capture == "meta":
            text = " ".join("".join(self.buffer).split())
            import re
            score = re.search(r"(\d+)\s*▲", text); comments = re.search(r"💬\s*(\d+)", text)
            author = re.search(r"by\s+(.+)$", text)
            if score: self.card["score"] = int(score.group(1))
            if comments: self.card["comments"] = int(comments.group(1))
            if author: self.card["author"] = author.group(1)
            self.capture = None
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.card["screenshot_available"] = bool(self.card.get("screenshot_path"))
                if "id" in self.card:
                    self.cards.append(self.card)
                self.card = None


def backfill_manifest(day):
    """Create a manifest from a legacy archive without network, screenshots, or copying."""
    folder = os.path.join(HISTORY_DIR, day)
    manifest = os.path.join(folder, "stories.json")
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as source: return json.load(source)
    parser = LegacyCardParser()
    try:
        with open(os.path.join(folder, "index.html"), encoding="utf-8") as source: parser.feed(source.read())
    except FileNotFoundError:
        return []
    for story in parser.cards:
        path = story.get("screenshot_path")
        story["screenshot_available"] = archived_screenshot_path(day, path) is not None
        if not story["screenshot_available"]:
            story["screenshot_path"] = None; story["screenshot_rank"] = None
    with open(manifest, "w", encoding="utf-8") as output: json.dump(parser.cards, output, indent=2)
    return parser.cards


def card_html(story, rank, image_src):
    image = f'<img src="{esc(image_src)}" alt="Screenshot" loading="lazy" />' if image_src else '<div class="fallback">Screenshot unavailable</div>'
    epoch = story.get("time", 0)
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M UTC") if epoch else ""
    url = story.get("url") or f"https://news.ycombinator.com/item?id={story['id']}"
    hn = f"https://news.ycombinator.com/item?id={story['id']}"
    return f'''<div class="card"><div class="img-wrap"><span class="rank">#{rank}</span>{image}<a href="{esc(url)}" target="_blank" rel="noopener" class="overlay">Open site ↗</a></div><div class="card-body"><a href="{esc(url)}" target="_blank" rel="noopener" class="title">{esc(story.get('title'))}</a><div class="meta"><span>{story.get('score', 0)} ▲</span><span>💬 {story.get('comments', 0)}</span><span>{stamp}</span><span>by {esc(story.get('author', 'anon'))}</span></div><a href="{hn}" target="_blank" rel="noopener" class="discuss">💬 Discuss on HN</a></div></div>'''


def picker_script(current_day="", current_week=""):
    return f'''<script>(function(){{var b="{BASE}",d=document.getElementById("date-select"),w=document.getElementById("week-select");fetch(b+"/days.json").then(r=>r.json()).then(days=>days.forEach(x=>{{var o=new Option(x,b+"/history/"+x+"/index.html",false,x==="{current_day}");d.add(o)}})).catch(()=>{{}});fetch(b+"/weeks.json").then(r=>r.json()).then(weeks=>weeks.forEach(x=>{{var o=new Option(x.label,b+"/weeks/"+x.key+"/index.html",false,x.key==="{current_week}");w.add(o)}})).catch(()=>{{}});}})();</script>'''


def page_html(title, stories, badge, current_day="", current_week=""):
    cards = "\n".join(card_html(s, i, f"screenshots/{s['screenshot_rank']}.jpg" if s.get("screenshot_available") else None) for i, s in enumerate(stories, 1))
    return f'''<!doctype html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#181817;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}header{{position:sticky;top:0;z-index:10;padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:#181817ee;border-bottom:1px solid #2e2e2d}}.home{{display:flex;align-items:center;gap:16px;color:#e0e0e0;text-decoration:none}}.logo{{background:#f60;color:#fff;font-size:22px;font-weight:bold;padding:6px 10px;border-radius:6px}}h1{{font-size:20px}}.badge{{background:#ff660022;color:#f60;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:bold}}.picker{{position:relative}}select{{background:#212120;color:#ccc;border:1px solid #2e2e2d;border-radius:8px;padding:5px 9px;font:14px inherit;max-width:230px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding:24px;max-width:1280px;margin:auto}}@media(max-width:1100px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}.card{{background:#212120;border:1px solid #2e2e2d;border-radius:12px;overflow:hidden}}.img-wrap{{position:relative;aspect-ratio:16/9;background:#1a1a19}}.img-wrap img{{width:100%;height:100%;object-fit:cover}}.rank{{position:absolute;z-index:1;top:10px;left:10px;background:#f60;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold}}.overlay{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#ff660099;color:#fff;text-decoration:none;opacity:0}}.card:hover .overlay{{opacity:1}}.fallback{{height:100%;display:flex;align-items:center;justify-content:center;color:#666}}.card-body{{padding:14px 16px}}.title{{color:#e0e0e0;text-decoration:none;font-weight:600}}.title:hover,.discuss{{color:#f60}}.meta{{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;color:#888;font-size:13px}}.discuss{{display:inline-block;margin-top:10px;font-size:13px}}footer{{padding:32px;text-align:center;color:#666}}</style></head><body><header><a href="{BASE}/index.html" class="home"><span class="logo">Y</span><h1>Hacker News — Top 50</h1></a><div class="picker"><select id="date-select" onchange="if(this.value)location.href=this.value"><option value="{BASE}/history/index.html">All history</option><option value="{BASE}/index.html">Today</option></select></div><span class="badge">{badge}</span><div class="picker"><select id="week-select" onchange="if(this.value)location.href=this.value"><option value="">Weeks</option></select></div></header>{picker_script(current_day,current_week)}<main class="grid">{cards}</main><footer>Data via Hacker News API · Screenshots via Microlink</footer></body></html>'''


def grouped_week_stories(days_and_stories):
    """Dedupe per ISO week, retaining highest score then later archive day."""
    groups = {}
    for day, stories in days_and_stories:
        if not parse_history_date(day): continue
        group = groups.setdefault(iso_week_key(day), {})
        for story in stories:
            old = group.get(story.get("id"))
            if old is None or story.get("score", 0) > old[1].get("score", 0) or (story.get("score", 0) == old[1].get("score", 0) and parse_history_date(day) > parse_history_date(old[0])):
                group[story.get("id")] = (day, story)
    result = {}
    for key, values in groups.items():
        records = []
        for day, story in values.values():
            record = dict(story); record["archive_day"] = day
            records.append(record)
        result[key] = sorted(records, key=lambda s: s.get("score", 0), reverse=True)
    return result


def archived_screenshot_path(day, path):
    """Return a verified regular screenshot file from a real archive directory."""
    if not (parse_history_date(day) and isinstance(path, str) and path):
        return None
    if os.path.isabs(path) or "\\" in path or "\x00" in path:
        return None
    parts = path.split("/")
    if not all(parts) or any(part in (".", "..") for part in parts):
        return None
    try:
        # Reject links at every boundary rather than allowing realpath() to hide them.
        # The archive day must be the direct, real child of a real history directory.
        if not os.path.isdir(HISTORY_DIR) or os.path.islink(HISTORY_DIR):
            return None
        archive_dir = os.path.join(HISTORY_DIR, day)
        if not os.path.isdir(archive_dir) or os.path.islink(archive_dir):
            return None

        image_path = archive_dir
        for index, part in enumerate(parts):
            image_path = os.path.join(image_path, part)
            if os.path.islink(image_path):
                return None
            if index < len(parts) - 1:
                if not os.path.isdir(image_path):
                    return None
            elif not os.path.isfile(image_path):
                return None

        resolved_history_dir = os.path.realpath(HISTORY_DIR)
        resolved_image_path = os.path.realpath(image_path)
        inside_history = os.path.commonpath((resolved_history_dir, resolved_image_path)) == resolved_history_dir
    except (OSError, ValueError):
        return None
    if not inside_history:
        return None
    return image_path


def archived_screenshot_url(story):
    """Return a weekly archive URL only for a verified, local archive image."""
    day = story.get("archive_day")
    path = story.get("screenshot_path")
    if not story.get("screenshot_available") or not archived_screenshot_path(day, path):
        return None
    return f"{BASE}/history/{day}/{urllib.parse.quote(path, safe='/')}"


def generate_weeks(days):
    manifests = [(day, backfill_manifest(day)) for day in days]
    groups = grouped_week_stories(manifests)
    os.makedirs(WEEKS_DIR, exist_ok=True)
    week_entries = []
    for key in sorted(groups, reverse=True):
        folder = os.path.join(WEEKS_DIR, key); os.makedirs(folder, exist_ok=True)
        stories = groups[key]
        cards = "\n".join(card_html(s, i, archived_screenshot_url(s)) for i, s in enumerate(stories, 1))
        # page_html's daily image resolver is intentionally bypassed: weekly paths are archive URLs only.
        page = page_html(f"Hacker News — {week_label(key)}", [], f"{len(stories)} stories", current_week=key)
        page = page.replace('<main class="grid"></main>', f'<main class="grid">{cards}</main>')
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as output: output.write(page)
        week_entries.append({"key": key, "label": week_label(key)})
    with open("weeks.json", "w", encoding="utf-8") as output: json.dump(week_entries, output, indent=2)


def generate_history_index(days):
    items = "".join(f'<a href="{BASE}/history/{d}/index.html">{d}</a><br>' for d in days)
    with open(os.path.join(HISTORY_DIR, "index.html"), "w", encoding="utf-8") as output:
        output.write(page_html("HN Daily Digest — History", [], f"{len(days)} days") .replace('<main class="grid"></main>', f'<main class="grid"><section>{items}</section></main>'))


def rebuild_from_archives():
    """Backfill legacy archives and rebuild all derived navigation/weekly files offline."""
    days = archive_days()
    for day in days:
        records = backfill_manifest(day)
        with open(os.path.join(HISTORY_DIR, day, "index.html"), "w", encoding="utf-8") as output:
            output.write(page_html(f"Hacker News — Top 50 — {day}", records, f"{len(records)} stories", current_day=day))
    # During an offline backfill, retain a usable root daily page based on the newest archive.
    if days:
        latest = backfill_manifest(days[0])
        with open("index.html", "w", encoding="utf-8") as output:
            output.write(page_html(f"Hacker News — Top 50 — {days[0]}", latest, f"{len(latest)} stories", current_day=days[0]))
    with open("days.json", "w", encoding="utf-8") as output:
        json.dump(days, output, indent=2)
    generate_weeks(days)
    generate_history_index(days)
    return days


def run():
    now = int(time.time()); today = datetime.now(timezone.utc).strftime("%d-%m-%Y")
    print("Fetching best stories...")
    best_ids = fetch_json("https://hacker-news.firebaseio.com/v0/beststories.json")[:200]
    def fetch_item(item_id):
        try: return fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        except Exception: return None
    with ThreadPoolExecutor(max_workers=20) as executor:
        items = [x for x in executor.map(fetch_item, best_ids) if x]
    top = sorted((x for x in items if x.get("type") == "story" and x.get("time", 0) >= now-86400 and x.get("score") is not None and not x.get("deleted") and not x.get("dead")), key=lambda x:x.get("score",0), reverse=True)[:50]
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    def screenshot(pair):
        rank, item = pair; url = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"; path = os.path.join(SCREENSHOTS_DIR, f"{rank}.jpg")
        try:
            api = f"https://api.microlink.io?url={urllib.parse.quote(url, safe='')}&screenshot=true&meta=false&embed=screenshot.url"
            with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent":"HNDigest/1.0"}), timeout=30) as resp, open(path,"wb") as file: file.write(resp.read())
            return rank, True
        except Exception as error: print(f"  [{rank}] FAILED: {error}"); return rank, False
    with ThreadPoolExecutor(max_workers=5) as executor: ok = {rank for rank, success in executor.map(screenshot, enumerate(top, 1)) if success}
    stories = [story_from_item(item, rank, rank in ok) for rank, item in enumerate(top, 1)]
    folder = os.path.join(HISTORY_DIR, today); os.makedirs(os.path.join(folder, "screenshots"), exist_ok=True)
    # Only this run's successful ranks are archived; old root files cannot leak into today's archive.
    for rank in ok: shutil.copy2(os.path.join(SCREENSHOTS_DIR, f"{rank}.jpg"), os.path.join(folder, "screenshots", f"{rank}.jpg"))
    with open(os.path.join(folder, "stories.json"), "w", encoding="utf-8") as output: json.dump(stories, output, indent=2)
    with open("index.html", "w", encoding="utf-8") as output: output.write(page_html(f"Hacker News — Top 50 — {today}", stories, f"{len(stories)} stories", current_day=today))
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as output: output.write(page_html(f"Hacker News — Top 50 — {today}", stories, f"{len(stories)} stories", current_day=today))
    rebuild_from_archives()
    print(f"Done! Generated {len(stories)} stories for {today}.")


if __name__ == "__main__":
    import sys
    if "--rebuild-archives" in sys.argv:
        print(f"Rebuilt {len(rebuild_from_archives())} archive days without network access.")
    else:
        run()
