import os, re, time, json, logging, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
RECIPIENTS = os.environ["RECIPIENTS"].split(",")
CHANNELS   = os.environ["CHANNELS"].split(",")
EMAIL      = os.environ.get("MYMEMORY_EMAIL", "")

# Fichier de memoire persistante (commite dans le repo par le workflow)
SEEN_FILE = "seen_guids.json"

def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)  # garde les 500 derniers

seen = load_seen()
is_first_run = len(seen) == 0
log.info(f"GUIDs en memoire : {len(seen)} | Premier lancement : {is_first_run}")

def get(url):
    r = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Bot/1.0"}), timeout=12)
    return r.read().decode("utf-8", errors="replace")

def translate(text):
    p = urllib.parse.urlencode({"q": text[:500], "langpair": "hy|fr"})
    if EMAIL: p += "&de=" + urllib.parse.quote(EMAIL)
    try:
        d = json.loads(get("https://api.mymemory.translated.net/get?" + p))
        if d.get("responseStatus") == 200:
            return d["responseData"]["translatedText"]
    except Exception as e:
        log.warning(f"Traduction: {e}")
    return text

def send(chat_id, text):
    urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}).encode(),
        headers={"Content-Type": "application/json"}
    ), timeout=10)

def poll(channel):
    channel = channel.strip()
    for base in ["https://rsshub.rssforever.com", "https://rss.shab.fun", "https://rsshub.app"]:
        try:
            xml = get(f"{base}/telegram/channel/{channel}")
            if "<item>" not in xml:
                continue
            items = []
            for item in ET.fromstring(xml).findall(".//item"):
                guid = item.findtext("guid") or item.findtext("link") or ""
                desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                    item.findtext("description") or item.findtext("title") or "")).strip()
                if desc:
                    items.append((guid, desc))
            log.info(f"RSS OK @{channel} — {len(items)} items via {base}")

            new = [(g, d) for g, d in items if g not in seen]
            for g, d in items:
                seen.add(g)

            if is_first_run:
                log.info(f"@{channel} — 1er lancement, {len(items)} msg indexes sans notif")
                return

            if not new:
                log.info(f"@{channel} — aucun nouveau message")
                return

            log.info(f"@{channel} — {len(new)} nouveau(x) message(s) a envoyer")
            for guid, original in new[:5]:
                tr = translate(original)
                msg = (f"<b>@{channel}</b>\n\n"
                       f"<i>Original :</i>\n{original[:300]}\n\n"
                       f"<b>Traduction :</b>\n{tr}")
                for rid in RECIPIENTS:
                    try:
                        send(rid.strip(), msg)
                        log.info(f"Envoye -> {rid.strip()}")
                    except Exception as e:
                        log.error(f"Erreur -> {rid}: {e}")
                    time.sleep(0.3)
                time.sleep(1.5)
            return
        except Exception as e:
            log.warning(f"{base}: {e}")

log.info("=== Demarrage ===")
for ch in CHANNELS:
    poll(ch)
    time.sleep(1)

save_seen(seen)
log.info(f"Termine. {len(seen)} GUIDs sauvegardes.")
