import json
import os
import urllib.request
import urllib.parse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

NOW = int(time.time())
CUTOFF = NOW - 86400
TODAY = datetime.now(timezone.utc).strftime("%d-%m-%Y")
SCREENSHOTS_DIR = "screenshots"

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HNDigest/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

print("Fetching best stories...")
best_ids = fetch_json("https://hacker-news.firebaseio.com/v0/beststories.json")[:200]

items = []
def fetch_item(item_id):
    try:
        return fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
    except:
        return None

with ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(fetch_item, iid): iid for iid in best_ids}
    for f in as_completed(futures):
        result = f.result()
        if result:
            items.append(result)

filtered = [
    it for it in items
    if it.get("type") == "story"
    and it.get("time", 0) >= CUTOFF
    and it.get("score") is not None
    and not it.get("deleted")
    and not it.get("dead")
]
filtered.sort(key=lambda x: x.get("score", 0), reverse=True)
top50 = filtered[:50]
print(f"Found {len(filtered)} stories from last 24h, taking top {len(top50)}")

# Download screenshots via Microlink
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def download_screenshot(args):
    idx, item = args
    url = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"
    filename = f"{SCREENSHOTS_DIR}/{idx}.jpg"
    api_url = f"https://api.microlink.io?url={urllib.parse.quote(url, safe='')}&screenshot=true&meta=false&embed=screenshot.url"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "HNDigest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(filename, "wb") as f:
                f.write(resp.read())
        print(f"  [{idx}] OK")
        return idx, True
    except Exception as e:
        print(f"  [{idx}] FAILED: {e}")
        return idx, False

print("Downloading screenshots...")
screenshot_ok = set()
with ThreadPoolExecutor(max_workers=5) as executor:
    tasks = [(i, item) for i, item in enumerate(top50, 1)]
    for idx, ok in executor.map(download_screenshot, tasks):
        if ok:
            screenshot_ok.add(idx)

print(f"Screenshots downloaded: {len(screenshot_ok)}/{len(top50)}")

def esc(s):
    if not s: return ""
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def fmt_time(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M UTC")

# Pre-compute history days for the dropdown
HISTORY_DIR = "history"
os.makedirs(HISTORY_DIR, exist_ok=True)

def parse_history_date(d):
    try:
        return datetime.strptime(d, "%d-%m-%Y")
    except ValueError:
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return datetime.min

history_days = sorted(
    [d for d in os.listdir(HISTORY_DIR) if os.path.isdir(os.path.join(HISTORY_DIR, d))],
    key=parse_history_date,
    reverse=True
)

BASE = "/hn-daily-digest"

# Build dropdown options for the date picker
dropdown_options = f'<option value="{BASE}/history/index.html">All history</option>\n'
dropdown_options += f'<option value="{BASE}/index.html">Today</option>\n'
for day in history_days:
    if day == TODAY:
        continue
    dropdown_options += f'<option value="{BASE}/history/{day}/index.html">{day}</option>\n'

cards = ""
for i, item in enumerate(top50, 1):
    title = esc(item.get("title", "Untitled"))
    url = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"
    score = item.get("score", 0)
    comments = item.get("descendants", 0)
    author = esc(item.get("by", "anon"))
    t = fmt_time(item.get("time", NOW))
    hn_link = f"https://news.ycombinator.com/item?id={item['id']}"

    if i in screenshot_ok:
        img_tag = f'<img src="screenshots/{i}.jpg" alt="Screenshot" loading="lazy" />'
    else:
        img_tag = '<div class="fallback">Screenshot unavailable</div>'

    cards += f'''
    <div class="card">
      <div class="img-wrap">
        <span class="rank">#{i}</span>
        {img_tag}
        <a href="{esc(url)}" target="_blank" rel="noopener" class="overlay">Open site ↗</a>
      </div>
      <div class="card-body">
        <a href="{esc(url)}" target="_blank" rel="noopener" class="title">{title}</a>
        <div class="meta">
          <span>{score} ▲</span>
          <span>💬 {comments}</span>
          <span>{t}</span>
          <span>by {author}</span>
        </div>
        <a href="{esc(hn_link)}" target="_blank" rel="noopener" class="discuss">💬 Discuss on HN</a>
      </div>
    </div>'''

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hacker News — Top 50 — {TODAY}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#181817;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh}}
header{{position:sticky;top:0;z-index:100;background:#181817ee;backdrop-filter:blur(10px);border-bottom:1px solid #2e2e2d;padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.logo{{background:#ff6600;color:#fff;font-weight:700;font-size:22px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:6px}}
header a.home{{text-decoration:none;display:flex;align-items:center;gap:16px}}
header h1{{font-size:20px;font-weight:600;color:#e0e0e0}}
header a.home:hover h1{{color:#ff6600}}
header a.home:hover .logo{{opacity:0.85}}
.date-picker{{position:relative;display:inline-block}}
.date-picker select{{appearance:none;-webkit-appearance:none;background:#212120;color:#888;border:1px solid #2e2e2d;border-radius:8px;padding:4px 28px 4px 10px;font-size:14px;font-family:inherit;cursor:pointer;outline:none}}
.date-picker select:hover{{border-color:#ff6600;color:#e0e0e0}}
.date-picker::after{{content:"▾";position:absolute;right:10px;top:50%;transform:translateY(-50%);color:#888;pointer-events:none;font-size:12px}}
.badge{{background:#ff660022;color:#ff6600;font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding:24px;max-width:1280px;margin:0 auto}}
@media(max-width:1100px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#212120;border:1px solid #2e2e2d;border-radius:12px;overflow:hidden;transition:border-color .2s,transform .2s}}
.card:hover{{border-color:#ff6600;transform:translateY(-2px)}}
.img-wrap{{position:relative;aspect-ratio:16/9;background:#1a1a19;overflow:hidden}}
.img-wrap img{{width:100%;height:100%;object-fit:cover;display:block}}
.rank{{position:absolute;top:10px;left:10px;background:#ff6600;color:#fff;font-size:12px;font-weight:700;padding:3px 8px;border-radius:6px;z-index:2}}
.overlay{{position:absolute;inset:0;background:#ff660099;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:600;font-size:15px;text-decoration:none;opacity:0;transition:opacity .2s}}
.card:hover .overlay{{opacity:1}}
.no-img{{background:#1a1a19}}
.fallback{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#555;font-size:14px}}
.card-body{{padding:14px 16px}}
.title{{color:#e0e0e0;text-decoration:none;font-weight:600;font-size:15px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.title:hover{{color:#ff6600}}
.meta{{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:13px;color:#888}}
.discuss{{display:inline-block;margin-top:10px;font-size:13px;color:#ff6600;text-decoration:none}}
.discuss:hover{{text-decoration:underline}}
footer{{text-align:center;padding:32px;color:#555;font-size:13px;border-top:1px solid #2e2e2d;margin-top:20px}}
footer a{{color:#ff6600;text-decoration:none}}
</style>
</head>
<body>
<header>
  <a href="{BASE}/index.html" class="home">
    <div class="logo">Y</div>
    <h1>Hacker News — Top 50</h1>
  </a>
  <div class="date-picker">
    <select onchange="if(this.value)window.location.href=this.value">
      <option value="" selected>{TODAY}</option>
      {dropdown_options}
    </select>
  </div>
  <span class="badge">{len(top50)} stories</span>
</header>
<main class="grid">
{cards}
</main>
<footer>
  Data via <a href="https://github.com/HackerNews/API" target="_blank">HN API</a> ·
  Screenshots via <a href="https://microlink.io" target="_blank">Microlink</a> ·
  Auto-generated on {TODAY}
</footer>
</body>
</html>'''

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

# Copy today's screenshots to history
history_ss_dir = f"{HISTORY_DIR}/{TODAY}/screenshots"
os.makedirs(history_ss_dir, exist_ok=True)
import shutil
for fname in os.listdir(SCREENSHOTS_DIR):
    src = os.path.join(SCREENSHOTS_DIR, fname)
    dst = os.path.join(history_ss_dir, fname)
    if os.path.isfile(src):
        shutil.copy2(src, dst)

# Write history page with relative screenshot paths
history_html = html.replace('src="screenshots/', f'src="screenshots/')
with open(f"{HISTORY_DIR}/{TODAY}/index.html", "w", encoding="utf-8") as f:
    f.write(history_html)

# Refresh history_days after adding today
history_days = sorted(
    [d for d in os.listdir(HISTORY_DIR) if os.path.isdir(os.path.join(HISTORY_DIR, d))],
    key=parse_history_date,
    reverse=True
)

# Generate history index page
history_items = ""
for day in history_days:
    history_items += f'<a href="{BASE}/history/{day}/index.html" class="history-item">{day}</a>\n'

history_index = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HN Daily Digest — History</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#181817;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh}}
header{{position:sticky;top:0;z-index:100;background:#181817ee;backdrop-filter:blur(10px);border-bottom:1px solid #2e2e2d;padding:16px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.logo{{background:#ff6600;color:#fff;font-weight:700;font-size:22px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:6px}}
header h1{{font-size:20px;font-weight:600}}
.badge{{background:#ff660022;color:#ff6600;font-size:12px;font-weight:600;padding:4px 10px;border-radius:20px}}
.back{{color:#ff6600;text-decoration:none;font-size:14px}}
.back:hover{{text-decoration:underline}}
.list{{max-width:600px;margin:40px auto;padding:0 24px}}
.history-item{{display:block;padding:16px 20px;margin-bottom:8px;background:#212120;border:1px solid #2e2e2d;border-radius:10px;color:#e0e0e0;text-decoration:none;font-size:16px;font-weight:500;transition:border-color .2s}}
.history-item:hover{{border-color:#ff6600;color:#ff6600}}
footer{{text-align:center;padding:32px;color:#555;font-size:13px;border-top:1px solid #2e2e2d;margin-top:40px}}
footer a{{color:#ff6600;text-decoration:none}}
</style>
</head>
<body>
<header>
  <div class="logo">Y</div>
  <h1>HN Daily Digest — History</h1>
  <span class="badge">{len(history_days)} days</span>
  <a href="{BASE}/index.html" class="back">← Today</a>
</header>
<div class="list">
{history_items}
</div>
<footer>
  Data via <a href="https://github.com/HackerNews/API" target="_blank">HN API</a> ·
  Screenshots via <a href="https://microlink.io" target="_blank">Microlink</a>
</footer>
</body>
</html>'''

with open(f"{HISTORY_DIR}/index.html", "w", encoding="utf-8") as f:
    f.write(history_index)

print(f"Done! Generated {len(top50)} stories for {TODAY}")
print(f"History: {len(history_days)} days archived")
