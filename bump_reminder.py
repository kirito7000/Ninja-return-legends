#!/usr/bin/env python3
"""
Rappel de /bump : poste dans un salon Discord (via webhook) un message qui mentionne un rôle
et contient un lien cliquable vers la commande /bump. C'est TOI qui valides la commande :
le bump reste manuel (l'auto-bump est interdit par Disboard).

- Sans dépendance (Python 3.9+).
- À lancer souvent (toutes les 10 minutes environ) : le script ne poste que si l'intervalle
  BUMP_INTERVAL_MIN s'est écoulé depuis le dernier rappel (mémorisé dans bump_state.json).

Réglages (variables d'environnement) :
  BUMP_WEBHOOK_URL    webhook du salon (obligatoire)
  BUMP_ROLE_ID        identifiant du rôle à mentionner (facultatif)
  BUMP_COMMAND_ID     identifiant de la commande /bump (facultatif : sinon texte simple "/bump")
  BUMP_INTERVAL_MIN   minutes entre deux rappels (défaut 130 ; le délai Disboard est de 120)
  BUMP_QUIET_FROM / BUMP_QUIET_TO   heures « calmes » sans rappel, ex. 0 et 8 (facultatif)
  BUMP_TZ             fuseau pour les heures calmes (défaut Europe/Paris)

Options : --test (envoie un message tout de suite, sans toucher à l'état) ; --force (poste maintenant).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "bump_state.json"
USER_AGENT = "Mozilla/5.0 (compatible; BumpReminder/1.0)"


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def build_message() -> tuple[str, dict]:
    role = env("BUMP_ROLE_ID")
    command_id = env("BUMP_COMMAND_ID")
    mention = f"<@&{role}> " if role.isdigit() else ""
    command = f"</bump:{command_id}>" if command_id.isdigit() else "`/bump`"
    content = f"{mention}⏰ **C'est l'heure de bump le serveur !** Clique ici : {command}"
    allowed = {"parse": [], "roles": [role] if role.isdigit() else []}
    return content, allowed


def send(webhook: str, content: str, allowed: dict) -> None:
    payload = {"username": env("BOT_NAME", "Rappel bump"), "content": content, "allowed_mentions": allowed}
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20):
        pass


def is_quiet_now() -> bool:
    start, end = env("BUMP_QUIET_FROM"), env("BUMP_QUIET_TO")
    if not (start.isdigit() and end.isdigit()) or start == end:
        return False
    try:
        from zoneinfo import ZoneInfo
        hour = datetime.now(ZoneInfo(env("BUMP_TZ", "Europe/Paris"))).hour
    except Exception:  # fuseau indisponible : on ne bloque pas les rappels
        return False
    s, e = int(start), int(end)
    return (s <= hour < e) if s < e else (hour >= s or hour < e)


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="envoie un message de test, sans toucher à l'état")
    ap.add_argument("--force", action="store_true", help="poste maintenant, quel que soit le délai")
    args = ap.parse_args()

    webhook = env("BUMP_WEBHOOK_URL")
    if not webhook:
        print("BUMP_WEBHOOK_URL manquant", file=sys.stderr)
        return 1

    content, allowed = build_message()
    if args.test:
        send(webhook, content, allowed)
        print("Message de test envoyé.")
        return 0

    interval = max(60, int(env("BUMP_INTERVAL_MIN") or "130")) * 60
    now = time.time()
    last = float(read_state().get("last_ping", 0))

    if not args.force:
        if now - last < interval:
            print(f"Pas encore l'heure (dernier rappel il y a {int((now - last) // 60)} min).")
            return 0
        if is_quiet_now():
            print("Heures calmes : pas de rappel.")
            return 0

    try:
        send(webhook, content, allowed)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Échec d'envoi : {exc}", file=sys.stderr)
        return 0  # on réessaiera au prochain passage
    STATE_FILE.write_text(json.dumps({"last_ping": now}, indent=2), encoding="utf-8")
    print("Rappel envoyé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
