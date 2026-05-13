"""
Telegram Traducteur — Arménien → Français
Tourne 24/7 sur Render.com (gratuit)
- Déduplication par GUID + similarité de texte (Jaccard)
- Filtrage de pertinence automatique via Claude AI ou scoring basique
"""
import os
import re
import time
import json
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config depuis variables d'environnement ────────────────────────────────
BOT_TOKEN       = os.environ.get('BOT_TOKEN', '')
RECIPIENTS      = os.environ.get('RECIPIENTS', '')
CHANNELS        = os.environ.get('CHANNELS', '')
MYMEMORY_EMAIL  = os.environ.get('MYMEMORY_EMAIL', '')
CLAUDE_API_KEY  = os.environ.get('CLAUDE_API_KEY', '')   # optionnel — scoring IA
INTERVAL        = int(os.environ.get('INTERVAL', '300'))
MIN_SCORE       = int(os.environ.get('MIN_SCORE', '5'))          # score min /10
SIMILARITY_THR  = float(os.environ.get('SIMILARITY_THR', '0.7')) # seuil doublon
RUN_ONCE        = os.environ.get('RUN_ONCE', 'false').lower() == 'true'

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN manquant !")
if not RECIPIENTS:
    raise ValueError("RECIPIENTS manquant (ex: 123456789,987654321)")
if not CHANNELS:
    raise ValueError("CHANNELS manquant (ex: oragir_news,twentyfournews)")

recipient_ids = [r.strip() for r in RECIPIENTS.split(',') if r.strip()]
channel_names = [c.strip() for c in CHANNELS.split(',') if c.strip()]

log.info("=" * 55)
log.info("  Telegram Traducteur AM->FR")
log.info("=" * 55)
log.info(f"Canaux        : {', '.join(channel_names)}")
log.info(f"Destinataires : {len(recipient_ids)}")
log.info(f"Intervalle    : {INTERVAL}s")
log.info(f"Score min     : {MIN_SCORE}/10")
log.info(f"Seuil doublon : {int(SIMILARITY_THR*100)}%")
log.info(f"Scoring IA    : {'Claude (actif)' if CLAUDE_API_KEY else 'Basique (pas de cle Claude)'}")

# ── Etat ───────────────────────────────────────────────────────────────────
seen_guids  = set()
seen_texts  = []   # historique textes pour dedup (max 200)
first_run   = {ch: True for ch in channel_names}
stats       = {
    'fetched': 0, 'translated': 0, 'sent': 0,
    'skipped_dup': 0, 'skipped_score': 0, 'errors': 0
}

# ── HTTP helpers ───────────────────────────────────────────────────────────
def http_get(url, timeout=15, extra_headers=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; TelegramTranslator/1.0)',
        'Accept': '*/*',
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')

def http_post_json(url, data, timeout=15, extra_headers=None):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Bot/1.0'}
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

# ── Deduplication par similarite (Jaccard) ─────────────────────────────────
def normalize(text):
    t = text.lower()
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def jaccard(a, b):
    wa = set(normalize(a).split())
    wb = set(normalize(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)

def is_duplicate(text):
    for prev in seen_texts[-100:]:
        if jaccard(text, prev) >= SIMILARITY_THR:
            return True
    return False

def mark_seen(text):
    global seen_texts
    seen_texts.append(text)
    if len(seen_texts) > 200:
        seen_texts = seen_texts[-200:]

# ── Score de pertinence ────────────────────────────────────────────────────
def score_claude(text_fr):
    """Score via Claude Haiku — rapide et pas cher."""
    prompt = (
        "Tu es un filtre d'actualites armeniennes. "
        "Evalue la pertinence de ce texte traduit en francais.\n\n"
        f'Texte : "{text_fr[:400]}"\n\n'
        "Score de 1 a 10 :\n"
        "8-10 : Important (politique, guerre, catastrophe, election, economie majeure)\n"
        "5-7  : Interessant (societe, sport, culture, fait divers notable)\n"
        "1-4  : Peu utile (pub, spam, voeux, texte trop court, contenu banal)\n\n"
        'Reponds UNIQUEMENT en JSON : {"score": X, "raison": "..."}'
    )
    try:
        result = http_post_json(
            "https://api.anthropic.com/v1/messages",
            {
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': 80,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            extra_headers={
                'x-api-key': CLAUDE_API_KEY,
                'anthropic-version': '2023-06-01',
            }
        )
        raw = result['content'][0]['text'].strip()
        raw = re.sub(r'```json|```', '', raw).strip()
        parsed = json.loads(raw)
        return int(parsed.get('score', 5)), parsed.get('raison', '')
    except Exception as e:
        log.warning(f'Score Claude echoue: {e}')
        return None, None

def score_basic(text):
    """Score sans IA — mots-cles."""
    t = text.lower()
    score = 5
    for w in ['mort', 'tue', 'guerre', 'attaque', 'explosion', 'election',
              'president', 'accord', 'crise', 'urgence', 'arrestation',
              'manifestation', 'tremblement', 'accident', 'incendie',
              'assassinat', 'catastrophe', 'bombe', 'militaire', 'reforme']:
        if w in t:
            score = min(10, score + 2)
    for w in ['promo', 'solde', 'publicite', 'abonnez', 'abonnement',
              'bonne journee', 'bonjour', 'bonsoir', 'felicitations',
              'anniversaire', 'souhaits', 'voeux', 'cliquez', 'suivez',
              'instagram', 'facebook', 'youtube']:
        if w in t:
            score = max(1, score - 2)
    if len(text.split()) < 5:
        score = max(1, score - 2)
    return score, f'Mots-cles : {score}/10'

def get_score(text_fr):
    if CLAUDE_API_KEY:
        score, raison = score_claude(text_fr)
        if score is not None:
            return score, raison
    return score_basic(text_fr)

# ── RSS ────────────────────────────────────────────────────────────────────
RSSHUB_INSTANCES = [
    'https://rsshub.rssforever.com',
    'https://rss.shab.fun',
    'https://rsshub.rss3.workers.dev',
    'https://rsshub.app',
]

def strip_tags(html):
    clean = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', clean).strip()

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall('.//item'):
            guid    = item.findtext('guid') or item.findtext('link') or ''
            content = strip_tags(item.findtext('description') or '') or strip_tags(item.findtext('title') or '')
            link    = item.findtext('link') or ''
            pubdate = item.findtext('pubDate') or ''
            if content.strip():
                items.append({'guid': guid, 'content': content.strip(), 'link': link, 'pubDate': pubdate})
    except ET.ParseError as e:
        log.warning(f'Parsing RSS: {e}')
    return items

def fetch_rss(channel):
    for base in RSSHUB_INSTANCES:
        url = f'{base}/telegram/channel/{channel}'
        try:
            text = http_get(url, timeout=12)
            if '<item>' in text:
                log.info(f'RSS OK @{channel} via {base}')
                return parse_rss(text)
        except Exception as e:
            log.debug(f'@{channel} {base}: {e}')
    raise RuntimeError(f'Toutes les instances RSSHub ont echoue pour @{channel}')

# ── Traduction MyMemory ─────────────────────────────────────────────────────
def translate(text):
    params = urllib.parse.urlencode({'q': text[:500], 'langpair': 'hy|fr'})
    if MYMEMORY_EMAIL:
        params += '&de=' + urllib.parse.quote(MYMEMORY_EMAIL)
    try:
        resp = http_get(f'https://api.mymemory.translated.net/get?{params}', timeout=10)
        data = json.loads(resp)
        if data.get('responseStatus') == 200:
            return data['responseData']['translatedText']
    except Exception as e:
        log.warning(f'Traduction: {e}')
    return text

# ── Telegram ───────────────────────────────────────────────────────────────
def send_telegram(chat_id, text):
    result = http_post_json(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        {'chat_id': chat_id, 'text': text[:4000], 'parse_mode': 'HTML'}
    )
    if not result.get('ok'):
        raise RuntimeError(result.get('description', 'Erreur'))

def notify_all(channel, original, translated, score, raison):
    emoji = '🔴' if score >= 8 else '🟡' if score >= 5 else '🟢'
    msg = (
        f"<b>📢 @{channel}</b>  {emoji} <b>{score}/10</b>\n"
        f"<i>{raison}</i>\n\n"
        f"<i>🇦🇲 Original :</i>\n{original[:300]}\n\n"
        f"<b>🇫🇷 Traduction :</b>\n{translated}"
    )
    sent = 0
    for rid in recipient_ids:
        try:
            send_telegram(rid, msg)
            sent += 1
            time.sleep(0.3)
        except Exception as e:
            log.error(f'Notif -> {rid}: {e}')
            stats['errors'] += 1
    return sent

# ── Boucle principale ──────────────────────────────────────────────────────
def poll_channel(channel):
    try:
        items = fetch_rss(channel)
    except Exception as e:
        log.error(f'@{channel}: {e}')
        stats['errors'] += 1
        return

    new_items = [i for i in items if i['guid'] not in seen_guids]
    for i in items:
        seen_guids.add(i['guid'])

    if first_run[channel]:
        first_run[channel] = False
        for i in items:
            mark_seen(i['content'])
        log.info(f'@{channel} — {len(items)} msg(s) indexes (1er passage, pas de notif)')
        return

    if not new_items:
        log.info(f'@{channel} — aucun nouveau message')
        return

    log.info(f'@{channel} — {len(new_items)} nouveau(x) a analyser')

    for item in new_items[:5]:
        original = item['content']
        stats['fetched'] += 1

        # 1. Dedup similarite
        if is_duplicate(original):
            stats['skipped_dup'] += 1
            log.info(f'[DOUBLON] "{original[:60]}"')
            mark_seen(original)
            continue

        # 2. Traduction
        translated = translate(original)
        if translated != original:
            stats['translated'] += 1

        # 3. Score pertinence
        score, raison = get_score(translated)
        log.info(f'[SCORE {score}/10] {raison} — "{translated[:50]}..."')

        # 4. Filtre
        if score < MIN_SCORE:
            stats['skipped_score'] += 1
            log.info(f'[IGNORE] Score {score} < {MIN_SCORE}')
            mark_seen(original)
            continue

        # 5. Envoi
        mark_seen(original)
        sent = notify_all(channel, original, translated, score, raison)
        if sent > 0:
            stats['sent'] += sent

        time.sleep(1.5)

def print_stats():
    log.info(
        f"[STATS] Recus:{stats['fetched']} | "
        f"Traduits:{stats['translated']} | "
        f"Envoyes:{stats['sent']} | "
        f"Doublons:{stats['skipped_dup']} | "
        f"Score bas:{stats['skipped_score']} | "
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
        # Mode GitHub Actions : un seul cycle puis on s'arrête
        if RUN_ONCE:
            log.info("Mode cycle unique (GitHub Actions) — arret.")
            break
        log.info(f"Prochain cycle dans {INTERVAL}s...")
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
