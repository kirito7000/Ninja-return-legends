#!/usr/bin/env python3
"""
Bot Discord : surveille la version de Ninja Return Legend(s) sur un site web
et envoie un message dans un salon Discord dès que la version change.

- Aucune dépendance : Python 3.8+ suffit (bibliothèque standard uniquement).
- Envoie les messages via un webhook Discord (pas besoin d'héberger un vrai bot).
- Configuration dans le fichier .env (voir .env.example).

Utilisation :
    python3 ninja_version_bot.py            # tourne en continu
    python3 ninja_version_bot.py --once     # une seule vérification (pour cron)
    python3 ninja_version_bot.py --test     # envoie un message de test sur Discord
    python3 ninja_version_bot.py --show     # affiche la version trouvée, sans notifier
"""
import argparse
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER_AGENT = "Mozilla/5.0 (compatible; NinjaVersionBot/1.0)"

# Cherche "version 1.2.3" / "v1.2.3" / "Version : 1.2.3"
DEFAULT_REGEX = r"(?i)\b(?:version|ver\.?|v)\s*[:\-]?\s*(\d+(?:\.\d+){1,3})"
# Repli si rien n'est trouvé : n'importe quel numéro du type 1.2.3
FALLBACK_REGEX = r"\b(\d+\.\d+(?:\.\d+){1,2})\b"


def load_env(path: Path) -> None:
    """Charge un fichier .env minimaliste (CLE=valeur) dans os.environ."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def fetch_page(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page))


def extract_json_info(page: str) -> tuple[str, str | None] | None:
    """Si la page est un JSON du type {"version": "3.1.35", "downloadUrl": "..."}."""
    try:
        data = json.loads(page)
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("version"):
        url = data.get("downloadUrl") or data.get("download_url")
        return str(data["version"]).strip(), (str(url) if url else None)
    return None


def extract_version(page: str, pattern: str) -> str | None:
    """Retourne le premier groupe capturé (ou la correspondance entière)."""
    info = extract_json_info(page)
    if info:
        return info[0]
    for text in (page, html_to_text(page)):
        for regex in (pattern, FALLBACK_REGEX if pattern == DEFAULT_REGEX else None):
            if not regex:
                continue
            m = re.search(regex, text)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
    return None


def send_discord(webhook: str, content: str, embed: dict | None = None) -> None:
    payload = {"username": os.environ.get("BOT_NAME", "Ninja Return Legend"), "content": content}
    if embed:
        payload["embeds"] = [embed]
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20):
        pass


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def check_once(cfg: dict) -> None:
    page = fetch_page(cfg["url"])
    version = extract_version(page, cfg["regex"])
    json_info = extract_json_info(page)
    download_url = json_info[1] if json_info else None
    if not version:
        logging.warning("Aucune version trouvée sur %s (vérifie VERSION_REGEX).", cfg["url"])
        return

    state = read_state(cfg["state_file"])
    old = state.get("version")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if old == version:
        logging.info("Version inchangée : %s", version)
        return

    if old is None:
        logging.info("Première exécution, version enregistrée : %s", version)
        if cfg["announce_first"]:
            send_discord(cfg["webhook"], f"{cfg['mention']}Version actuelle de **{cfg['game']}** : `{version}`".strip())
    else:
        logging.info("Nouvelle version : %s -> %s", old, version)
        embed = {
            "title": f"🥷 Mise à jour de {cfg['game']} !",
            "description": f"La version est passée de `{old}` à **`{version}`**.",
            "url": os.environ.get("PAGE_URL", "").strip() or "https://nrlegend.com/",
            "color": 0xE67E22,
            "timestamp": now,
        }
        if download_url:
            embed["fields"] = [{"name": "Téléchargement", "value": download_url}]
        send_discord(cfg["webhook"], f"{cfg['mention']}Une nouvelle version est disponible !".strip(), embed)

    write_state(cfg["state_file"], {"version": version, "previous": old, "last_check": now, "changed_at": now})


def build_config() -> dict:
    load_env(HERE / ".env")
    cfg = {
        "url": os.environ.get("VERSION_URL", "").strip(),
        "regex": os.environ.get("VERSION_REGEX", "").strip() or DEFAULT_REGEX,
        "webhook": os.environ.get("DISCORD_WEBHOOK_URL", "").strip(),
        "interval": max(60, int(os.environ.get("CHECK_INTERVAL_SECONDS", "600"))),
        "game": os.environ.get("GAME_NAME", "Ninja Return Legend"),
        "announce_first": os.environ.get("ANNOUNCE_FIRST_RUN", "true").lower() in ("1", "true", "yes", "oui"),
        "state_file": HERE / os.environ.get("STATE_FILE", "state.json"),
    }
    role = os.environ.get("MENTION_ROLE_ID", "").strip()
    cfg["mention"] = f"<@&{role}> " if role.isdigit() else ""
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="une seule vérification puis quitte")
    ap.add_argument("--test", action="store_true", help="envoie un message de test sur Discord")
    ap.add_argument("--show", action="store_true", help="affiche la version détectée sans notifier")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = build_config()

    if args.test:
        if not cfg["webhook"]:
            logging.error("DISCORD_WEBHOOK_URL manquant dans .env")
            return 1
        send_discord(cfg["webhook"], "✅ Le bot de version fonctionne !")
        logging.info("Message de test envoyé.")
        return 0

    if not cfg["url"]:
        logging.error("VERSION_URL manquant dans .env (adresse de la page qui affiche la version).")
        return 1

    if args.show:
        print(extract_version(fetch_page(cfg["url"]), cfg["regex"]) or "Aucune version trouvée")
        return 0

    if not cfg["webhook"]:
        logging.error("DISCORD_WEBHOOK_URL manquant dans .env")
        return 1

    while True:
        try:
            check_once(cfg)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logging.warning("Erreur réseau : %s (nouvel essai au prochain cycle)", exc)
        except Exception:  # ne jamais laisser le bot mourir
            logging.exception("Erreur inattendue")
        if args.once:
            return 0
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    sys.exit(main())
