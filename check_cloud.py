#!/usr/bin/env python3
"""
Cloud restock checker (runs on GitHub Actions every ~5 min, 24/7).

Watches Apple Canada's refurbished store and pushes phone notifications via ntfy.sh:
  - 14" MacBook Pro, M5 Pro, standard display (no nano-texture), under $3,000 CAD
  - iPhone 17 Pro (not Max), 512GB or 1TB, any price
Only alerts when Apple's product page says it's really buyable (the listing page lags).
Re-alerts if a unit goes out of stock (someone's checkout) and comes back.
Deadline 2026-10-10 18:00 Atlantic: if no Mac matched, one alert with the best buyable backup.
Night (23:00-07:00 Atlantic): alerts are sent silently (low priority).

Env: NTFY_TOPIC (required to send), DRY_RUN=1 to only print.
State: state.json (committed back to the repo by the workflow).
"""
import datetime as dt, json, os, re, sys, urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Halifax")
MAC_URL = "https://www.apple.com/ca/shop/refurbished/mac/macbook-pro"
IPHONE_URL = "https://www.apple.com/ca/shop/refurbished/iphone"
MAX_PRICE = 3000.00
DEADLINE = dt.datetime(2026, 10, 10, 18, 0, tzinfo=TZ)
BACKUP_MAX_PRICE = 3200.00   # same as the Mac checker (nano $3,159); raise to 3700 to allow the 2TB $3,609
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
TOPIC = os.environ.get("NTFY_TOPIC", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1" or "--dry-run" in sys.argv
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def now():
    return dt.datetime.now(TZ)


def log(msg):
    print(now().strftime("%Y-%m-%d %H:%M:%S ") + msg, flush=True)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def listings(html):
    i = html.find("REFURB_GRID_BOOTSTRAP")
    out = []
    if i >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(html, html.find("{", i))
            for t in data.get("tiles", []):
                try:
                    price = float(t["price"]["currentPrice"]["raw_amount"])
                except (KeyError, TypeError, ValueError):
                    continue
                url = t.get("productDetailsUrl", "").split("?")[0]
                if url.startswith("/"):
                    url = "https://www.apple.com" + url
                out.append((t.get("title", ""), price, url, t.get("partNumber", "")))
        except ValueError:
            pass
    if out:
        return out
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        if d.get("@type") == "Product":
            o = (d.get("offers") or [{}])[0]
            out.append((d.get("name", ""), float(o.get("price", 0) or 0), d.get("url", ""), o.get("sku", "")))
    return out


def norm(s):
    return " ".join(s.replace("\xa0", " ").split())


def want_mac(n, p):
    return ("14-inch MacBook Pro" in n and "M5 Pro chip" in n
            and "nano-texture" not in n.lower() and 0 < p < MAX_PRICE)


def want_iphone(n, p):
    n = norm(n)
    return "iPhone 17 Pro" in n and "Max" not in n and ("512GB" in n or "1TB" in n) and p > 0


def buyable(link):
    """True / False from the product page; None if unknown (then trust the listing)."""
    try:
        h = fetch(link)
    except Exception:
        return None
    c = re.search(r'"customerCommitString":"([^"]*)"', h)
    if c and "out of stock" in c.group(1).lower():
        return False
    m = re.search(r'"isBuyable":(true|false)', h)
    return (m.group(1) == "true") if m else None


def push(title, body, link):
    night = not (7 <= now().hour < 23)
    log(f"PUSH{' (silent, night)' if night else ''}: {title} | {body} | {link}")
    if DRY_RUN or not TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}", data=body.encode("utf-8"), method="POST",
        headers={"Title": title.encode("ascii", "ignore").decode(), "Click": link,
                 "Priority": "low" if night else "urgent", "Tags": "computer,rotating_light",
                 "Actions": f"view, Open Apple page, {link}"})
    urllib.request.urlopen(req, timeout=20).read()


def short(n):
    return norm(n.replace("Refurbished ", "").replace("MacBook Pro Apple ", ""))


def watch(label, url, want, st):
    """st = state dict for this watch: {"seen": {sku: {"buyable": bool}}, "fails": int}"""
    try:
        items = listings(fetch(url))
    except Exception as e:
        log(f"[{label}] fetch failed: {e}")
        items = []
    if not items:
        st["fails"] = st.get("fails", 0) + 1
        log(f"[{label}] couldn't read listings - failure #{st['fails']} in a row")
        if st["fails"] == 6:   # ~30 min of failures: warn once
            push(f"{label} checker can't read Apple's page",
                 "6 checks in a row failed. Apple may be blocking the cloud checker or changed its page.", url)
        return None, []
    st["fails"] = 0
    seen = st.setdefault("seen", {})
    listed = [m for m in items if want(m[0], m[1])]
    status, matches, new = {}, [], []
    for m in listed:
        b = buyable(m[2]) is not False
        status[m[3]] = b
        prev = seen.get(m[3])
        if b:
            matches.append(m)
            if prev is None or prev.get("buyable") is False:
                new.append((m, prev is not None))
        elif prev is None or prev.get("buyable"):
            log(f"[{label}] {m[3]} listed but OUT OF STOCK (sold or in someone's checkout)")
    for s in seen:
        if s not in status:
            log(f"[{label}] {s} no longer listed (sold for good)")
    st["seen"] = {s: {"buyable": b} for s, b in status.items()}
    log(f"[{label}] {len(items)} listings, {len(listed)} listed match, {len(matches)} buyable, {len(new)} to alert")
    if new:
        new.sort(key=lambda x: x[0][1])
        (n, p, link, sku), again = new[0]
        head = "AVAILABLE AGAIN" if any(a for _, a in new) else "IN STOCK"
        extra = f" (+{len(new) - 1} more)" if len(new) > 1 else ""
        push(f"Refurb {label} {head}: ${p:,.0f}{extra}", f"{short(n)} - buy fast!", link)
    return items, matches


def deadline(items, matches, st):
    if now() < DEADLINE or matches or st.get("deadline_sent") or items is None:
        return
    backups = sorted((p, n, u) for n, p, u, s in items
                     if "14-inch MacBook Pro" in n and "M5 Pro chip" in n
                     and "15‑Core CPU and 16‑Core GPU" in n and 0 < p <= BACKUP_MAX_PRICE)
    backups = [b for b in backups if buyable(b[2]) is not False]
    if backups:
        p, n, u = backups[0]
        push(f"Mac deadline: best backup ${p:,.0f}", f"The $2,969 one never came back. Backup: {short(n)}", u)
    else:
        push("Mac deadline reached", "No restock and no backup in Apple's refurb store - check Best Buy.", MAC_URL)
    st["deadline_sent"] = now().isoformat()


def main():
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if "--test-push" in sys.argv:
        push("Restock alert test", "If you see this, phone alerts work. You'll get one like it when the Mac is back.",
             MAC_URL)
        return
    items, matches = watch("Mac", MAC_URL, want_mac, state.setdefault("mac", {}))
    deadline(items, matches, state)
    watch("iPhone", IPHONE_URL, want_iphone, state.setdefault("iphone", {}))
    state["last_run"] = now().isoformat()
    if not DRY_RUN:
        json.dump(state, open(STATE, "w"), indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
