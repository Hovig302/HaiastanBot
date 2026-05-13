"""
Telegram Traducteur — Arménien → Français
Tourne 24/7 sur Railway.app (gratuit)
"""
import os
import time
import json
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config depuis variables d'environnement ────────────────────────────────
BOT_TOKEN      = os.environ.get('BOT_TOKEN', '')
RECIPIENTS     = os.environ.get('RECIPIENTS', '')       # "123456789,987654321"
CHANNELS       = os.environ.get('CHANNELS', '')         # "oragir_news,twentyfournews"
MYMEMORY_EMAIL = os.environ.get('MYMEMORY_EMAIL', '')   # optionnel
INTERVAL       = int(os.environ.get('INTERVAL', '300')) # secondes (défaut 5 min)

# Validation
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN manquant dans les variables d'environnement !")
if not RECIPIENTS:
    raise ValueError("RECIPIENTS manquant (ex: 123456789,987654321)")
if not CHANNELS:
    raise ValueError("CHANNELS manquant (ex: oragir_news,twentyfournews)")

recipient_ids = [r.strip() for r in RECIPIENTS.split(',') if r.strip()]
channel_names = [c.strip() for c in CHANNELS.split(',') if c.strip()]

log.info(f"Bot démarré — {len(channel_names)} canal(aux), {len(recipient_ids)} destinataire(s)")
log.info(f"Canaux : {', '.join(channel_names)}")
log.info(f"Intervalle : {INTERVAL}s")

# ── État ───────────────────────────────────────────────────────────────────
seen_guids = set()
first_run   = {ch: True for ch in channel_names}
stats       = {'fetched': 0, 'translated': 0, 'sent': 0, 'errors': 0}

# ── Helpers HTTP ───────────────────────────────────────────────────────────
def http_get(url, timeout=15, headers=None):
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; TelegramTranslator/1.0)',
        'Accept': '*/*',
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')

def http_post_json(url, data, timeout=15):
    body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'TelegramBot/1.0',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

# ── RSS ────────────────────────────────────────────────────────────────────
RSSHUB_INSTANCES = [
    'https://rsshub.rssforever.com',
    'https://rss.shab.fun',
    'https://rsshub.rss3.workers.dev',
    'https://rsshub.app',
]

def fetch_rss(channel):
    for base in RSSHUB_INSTANCES:
        url = f'{base}/telegram/channel/{channel}'
        try:
            text = http_get(url, timeout=12)
            if '<item>' in text:
                log.info(f'RSS OK @{channel} via {base}')
                return parse_rss(text)
            else:
                log.debug(f'RSS vide @{channel} via {base}')
        except Exception as e:
            log.debug(f'RSS erreur @{channel} via {base}: {e}')
    raise RuntimeError(f'Toutes les instances RSSHub ont échoué pour @{channel}')

def strip_tags(text):
    """Supprime les balises HTML basiques."""
    import re
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for item in root.findall('.//item'):
            guid    = (item.findtext('guid') or item.findtext('link') or '')
            title   = item.findtext('title') or ''
            desc    = item.findtext('description') or ''
            link    = item.findtext('link') or ''
            pubdate = item.findtext('pubDate') or ''
            content = strip_tags(desc) or strip_tags(title)
            if content.strip():
                items.append({
                    'guid': guid,
                    'content': content.strip(),
                    'link': link,
                    'pubDate': pubdate,
                })
    except ET.ParseError as e:
        log.warning(f'Erreur parsing RSS: {e}')
    return items

# ── Traduction MyMemory ─────────────────────────────────────────────────────
def translate(text):
    chunk = text[:500]
    params = urllib.parse.urlencode({'q': chunk, 'langpair': 'hy|fr'})
    if MYMEMORY_EMAIL:
        params += '&de=' + urllib.parse.quote(MYMEMORY_EMAIL)
    url = f'https://api.mymemory.translated.net/get?{params}'
    try:
        resp = http_get(url, timeout=10)
        data = json.loads(resp)
        if data.get('responseStatus') == 200:
            return data['responseData']['translatedText']
        else:
            log.warning(f'MyMemory status {data.get("responseStatus")}: {data.get("responseDetails")}')
    except Exception as e:
        log.warning(f'Traduction échouée: {e}')
    return text  # retourne l'original si échec

# ── Telegram ────────────────────────────────────────────────────────────────
def send_telegram(chat_id, text):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    # Telegram limite à 4096 caractères
    text = text[:4000]
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    result = http_post_json(url, data, timeout=10)
    if not result.get('ok'):
        raise RuntimeError(result.get('description', 'Erreur inconnue'))
    return result

def notify_all(channel, original, translated):
    msg = (
        f"<b>📢 @{channel}</b>\n\n"
        f"<i>🇦🇲 Original :</i>\n{original[:400]}\n\n"
        f"<b>🇫🇷 Traduction :</b>\n{translated}"
    )
    sent = 0
    for rid in recipient_ids:
        try:
            send_telegram(rid, msg)
            sent += 1
            log.info(f'Notif envoyée → {rid}')
            time.sleep(0.3)
        except Exception as e:
            log.error(f'Notif échouée → {rid}: {e}')
            stats['errors'] += 1
    return sent

# ── Boucle principale ───────────────────────────────────────────────────────
def poll_channel(channel):
    global seen_guids

    try:
        items = fetch_rss(channel)
    except Exception as e:
        log.error(f'@{channel} RSS: {e}')
        stats['errors'] += 1
        return

    new_items = [i for i in items if i['guid'] not in seen_guids]
    for i in items:
        seen_guids.add(i['guid'])

    # Premier passage : on indexe sans notifier
    if first_run[channel]:
        first_run[channel] = False
        log.info(f'@{channel} — {len(items)} msg(s) indexés (premier passage, pas de notif)')
        return

    if not new_items:
        log.info(f'@{channel} — aucun nouveau message')
        return

    log.info(f'@{channel} — {len(new_items)} nouveau(x) message(s)')

    for item in new_items[:5]:  # max 5 par cycle
        original = item['content']
        stats['fetched'] += 1

        # Traduction
        translated = translate(original)
        if translated != original:
            stats['translated'] += 1
            log.info(f'Traduit: "{translated[:60]}..."')
        else:
            log.warning(f'Traduction non disponible, envoi original')

        # Notification
        sent = notify_all(channel, original, translated)
        if sent > 0:
            stats['sent'] += sent

        time.sleep(1.5)  # pause entre messages

def print_stats():
    log.info(
        f"Stats — Reçus: {stats['fetched']} | "
        f"Traduits: {stats['translated']} | "
        f"Envoyés: {stats['sent']} | "
        f"Erreurs: {stats['errors']}"
    )

def main():
    log.info("=" * 50)
    log.info("  Telegram Traducteur AM→FR  ")
    log.info("=" * 50)
    cycle = 0

    while True:
        cycle += 1
        log.info(f"── Cycle #{cycle} ──────────────────────────")

        for channel in channel_names:
            poll_channel(channel)
            time.sleep(1)

        print_stats()
        log.info(f"Prochain cycle dans {INTERVAL}s...")
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
