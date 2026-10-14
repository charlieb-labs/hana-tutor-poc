"""Reads Hana's conversations from Tavus and writes sessions.json for the page.
Only the math recap is kept: when, session, length, summary, next step. No names, video or perception data."""
import json, os, re, time, urllib.request
from datetime import datetime, timezone

PAL_ID = "p4f80ddd9679"
API = "https://tavusapi.com/v2"
KEY = os.environ["TAVUS_API_KEY"]
OUT = os.path.join(os.path.dirname(__file__), "..", "sessions.json")

def get(path):
    req = urllib.request.Request(API + path, headers={"x-api-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def ts(s):
    if not s: return None
    s = str(s).replace("+00:00Z", "Z").replace(" ", "T")
    s = re.sub(r"(\.\d{6})\d*", r"\1", s).replace("Z", "+00:00")
    if not re.search(r"[+-]\d\d:\d\d$", s): s += "+00:00"
    try: return datetime.fromisoformat(s)
    except ValueError: return None

def line(text, label):
    m = re.search(r"^" + label + r":\s*(.+)$", text or "", re.M)
    return m.group(1).strip() if m else ""

def session_name(recap_session, ctx):
    ctx = ctx or ""
    for k, v in [("Lesson 1", "Lesson 1 · Equivalent fractions"), ("Lesson 2", "Lesson 2 · Adding fractions"),
                 ("Lesson 3", "Lesson 3 · Subtracting fractions"), ("Lesson 4", "Lesson 4 · Word problems")]:
        if k in ctx: return v
    if "My week" in ctx or re.search("my week", recap_session or "", re.I): return "My week"
    if re.search("adding", ctx, re.I): return "Lesson 2 · Adding fractions"
    return recap_session or "Session"

def load():
    try:
        with open(OUT) as f: return json.load(f)
    except (FileNotFoundError, ValueError): return []

def update(rows):
    rows[:] = [r for r in rows if "stuck" in r]  # refetch rows saved before the stuck field existed
    seen = {r["id"] for r in rows}
    added = 0
    convs = get("/conversations?limit=50").get("data", [])
    for c in convs:
        if (c.get("pal_id") or c.get("persona_id")) != PAL_ID or c.get("status") != "ended" or c["conversation_id"] in seen:
            continue
        d = get(f"/conversations/{c['conversation_id']}?verbose=true")
        evs = d.get("events") or []
        ev = lambda t: next((e for e in evs if e.get("event_type") == t), None)
        post = ev("application.post_call_action_executed")
        ended = ts((ev("system.shutdown") or {}).get("timestamp")) or ts(d.get("updated_at"))
        if not post and ended and (datetime.now(timezone.utc) - ended).total_seconds() < 15 * 60:
            continue  # recap not ready yet, try next run
        plain = ""
        if post:
            body = (post.get("properties") or {}).get("request", {}).get("body")
            try: body = json.loads(body) if isinstance(body, str) else body
            except ValueError: body = {}
            plain = (body or {}).get("body") or ""
        start = ts((ev("system.replica_joined") or {}).get("timestamp")) or ts(d.get("created_at"))
        rows.append({"id": c["conversation_id"], "when": d.get("created_at"),
                     "session": session_name(line(plain, "Session"), d.get("conversational_context")),
                     "length_sec": round((ended - start).total_seconds()) if start and ended else None,
                     "summary": line(plain, "Summary"), "stuck": line(plain, "Got stuck on"),
                     "next": line(plain, "Next session")})
        added += 1
    return added

rows = load()
tries = 8 if os.environ.get("WAIT_FOR_NEW") == "1" else 1
for i in range(tries):
    if update(rows) or i == tries - 1: break
    time.sleep(30)  # called right after a call: wait for the recap
rows.sort(key=lambda r: r["when"] or "", reverse=True)
with open(OUT, "w") as f: json.dump(rows, f, indent=1, ensure_ascii=False)
print(len(rows), "sessions")
