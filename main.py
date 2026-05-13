"""
Telegram Traducteur — Armenien -> Francais
Tourne sur GitHub Actions (gratuit)
"""
import os
import re
import time
import json
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
RECIPIENTS     = os.environ.get("RECIPIENTS", "")
CHANNELS       = os.environ.get("CHANNELS", "")
MYMEMORY_EMAIL = os.environ.get("MYMEMORY_EMAIL", "")
INTERVAL       = int(os.environ.get("INTERVAL", "300"))
RUN_ONCE       = os.environ.get("RUN_ONCE", "false").lower() == "true"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN manquant !")
if not RECIPIENTS:
    raise ValueError("RECIPIENTS manquant !")
if not CHANNELS:
    raise ValueError("CHANNELS manquant !")

recipient_ids = [r.strip() for r in RECIPIENTS.split(",") if r.strip()]
channel_names = [c.strip() for c in CHANNELS.split(",") if c.strip()]

log.info("=" * 50)
log.info("  Telegram Traducteur AM->FR")
log.info("=" * 50)
log.info(f"Canaux        : {', '.join(channel_names)}")
log.info(f"Destinataires : {len(recipient_ids)}")
log.info(f"Intervalle    : {INTERVAL}s")
log.info(f"Mode          : {'GitHub Actions (cycle unique)' if RUN_ONCE else 'Continu'}")

# ── Etat ───────────────────────────────────────────────────────────────────
seen_guids = set()
first_run  = {ch: True for ch in channel_names}
stats      = {"fetched": 0, "translated": 0, "sent": 0, "errors": 0}

# ── HTTP ───────────────────────────────────────────────────────────────────
def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TelegramTranslator/1.0)",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def http_post_json(url, data, timeout=15, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

# ── RSS ────────────────────────────────────────────────────────────────────
RSSHUB_INSTANCES = [
    "https://rsshub.rssforever.com",
    "https://rss.shab.fun",
    "https://rsshub.rss3.workers.dev",
    "https://rsshub.app",
]

def strip_tags(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            guid    = item.findtext("guid") or item.findtext("link") or ""
            content = strip_tags(item.findtext("description") or "") or strip_tags(item.findtext("title") or "")
            link    = item.findtext("link") or ""
            pubdate = item.findtext("pubDate") or ""
            if content.strip():
                items.append({"guid": guid, "content": content.strip(), "link": link, "pubDate": pubdate})
    except ET.ParseError as e:
        log.warning(f"Parsing RSS: {e}")
    return items

def fetch_rss(channel):
    for base in RSSHUB_INSTANCES:
        url = f"{base}/telegram/channel/{channel}"
        try:
            text = http_get(url, timeout=12)
            if "<item>" in text:
                log.info(f"RSS OK @{channel} via {base}")
                return parse_rss(text)
        except Exception as e:
            log.debug(f"@{channel} {base}: {e}")
    raise RuntimeError(f"Toutes les instances RSSHub ont echoue pour @{channel}")

# ── Traduction ─────────────────────────────────────────────────────────────
def translate(text):
    params = urllib.parse.urlencode({"q": text[:500], "langpair": "hy|fr"})
    if MYMEMORY_EMAIL:
        params += "&de=" + urllib.parse.quote(MYMEMORY_EMAIL)
    try:
        resp = http_get(f"https://api.mymemory.translated.net/get?{params}", timeout=10)
        data = json.loads(resp)
        if data.get("responseStatus") == 200:
            return data["responseData"]["translatedText"]
    except Exception as e:
        log.warning(f"Traduction: {e}")
    return text

# ── Telegram ───────────────────────────────────────────────────────────────
def send_telegram(chat_id, text):
    result = http_post_json(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Erreur"))

def notify_all(channel, original, translated):
    msg = (
        f"<b>@{channel}</b>\n\n"
        f"<i>Armenien :</i>\n{original[:300]}\n\n"
        f"<b>Francais :</b>\n{translated}"
    )
    sent = 0
    for rid in recipient_ids:
        try:
            send_telegram(rid, msg)
            sent += 1
            log.info(f"Notif envoyee -> {rid}")
            time.sleep(0.3)
        except Exception as e:
            log.error(f"Notif -> {rid}: {e}")
            stats["errors"] += 1
    return sent

# ── Boucle ─────────────────────────────────────────────────────────────────
def poll_channel(channel):
    try:
        items = fetch_rss(channel)
    except Exception as e:
        log.error(f"@{channel}: {e}")
        stats["errors"] += 1
        return

    new_items = [i for i in items if i["guid"] not in seen_guids]
    for i in items:
        seen_guids.add(i["guid"])

    if first_run[channel]:
        first_run[channel] = False
        log.info(f"@{channel} — {len(items)} msg(s) indexes (1er passage, pas de notif)")
        return

    if not new_items:
        log.info(f"@{channel} — aucun nouveau message")
        return

    log.info(f"@{channel} — {len(new_items)} nouveau(x) message(s)")

    for item in new_items[:5]:
        original = item["content"]
        stats["fetched"] += 1

        translated = translate(original)
        if translated != original:
            stats["translated"] += 1
            log.info(f"Traduit: \"{translated[:60]}\"")

        sent = notify_all(channel, original, translated)
        if sent > 0:
            stats["sent"] += sent

        time.sleep(1.5)

def print_stats():
    log.info(
        f"[STATS] Recus:{stats['fetched']} | "
        f"Traduits:{stats['translated']} | "
        f"Envoyes:{stats['sent']} | "
        f"Erreurs:{stats['errors']}"
    )

def main():
    cycle = 0
    while True:
        cycle += 1
        log.info(f"{'='*20} Cycle #{cycle} {'='*20}")
        for channel in channel_names:
            poll_channel(channel)
            time.sleep(1)
        print_stats()
        if RUN_ONCE:
            log.info("Cycle unique termine (GitHub Actions).")
            break
        log.info(f"Prochain cycle dans {INTERVAL}s...")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
