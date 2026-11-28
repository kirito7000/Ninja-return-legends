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


def send(webhook: str, content: str, allowed: dict) -> str | None:
    """Poste le message et retourne son identifiant (pour pouvoir le supprimer plus tard)."""
    payload = {"username": env("BOT_NAME", "Rappel bump"), "content": content, "allowed_mentions": allowed}
    req = urllib.request.Request(
        webhook + ("&" if "?" in webhook else "?") + "wait=true",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        try:
            return str(json.loads(resp.read().decode("utf-8")).get("id") or "") or None
        except ValueError:
            return None


def delete_message(webhook: str, message_id: str) -> None:
    """Supprime un ancien rappel (ignore les erreurs : message déjà supprimé, etc.)."""
    req = urllib.request.Request(
        f"{webhook}/messages/{message_id}", headers={"User-Agent": USER_AGENT}, method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            pass
    except (urllib.error.URLError, TimeoutError, OSError):
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


def run_link() -> str:
    """Lien vers le lancement GitHub Actions en cours (vide hors GitHub)."""
    server, repo, run_id = env("GITHUB_SERVER_URL"), env("GITHUB_REPOSITORY"), env("GITHUB_RUN_ID")
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


def log_line(text: str, routine: bool = False) -> None:
    """Écrit une ligne dans le salon de logs (LOG_WEBHOOK_URL), si configuré.

    routine=True : vérification « normale » sans rappel, envoyée seulement si BUMP_LOG_EVERY_CHECK est actif.
    """
    webhook = env("LOG_WEBHOOK_URL")
    if not webhook or (routine and env("BUMP_LOG_EVERY_CHECK", "false").lower() not in ("1", "true", "oui", "yes")):
        return
    link = run_link()
    line = f"<t:{int(time.time())}:T> {text}" + (f" · [détails]({link})" if link else "")
    try:
        send(webhook, line, {"parse": []})
    except (urllib.error.URLError, TimeoutError, OSError):
        pass


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
    state = read_state()
    last = float(state.get("last_ping", 0))

    if not args.force:
        if now - last < interval:
            minutes = int((now - last) // 60)
            print(f"Pas encore l'heure (dernier rappel il y a {minutes} min).")
            log_line(f"🔎 Rappel bump : pas encore l'heure (dernier rappel il y a {minutes} min)", routine=True)
            return 0
        if is_quiet_now():
            print("Heures calmes : pas de rappel.")
            log_line("🌙 Rappel bump : heures calmes, pas de rappel", routine=True)
            return 0

    # Un seul rappel visible à la fois : on supprime l'ancien avant d'en poster un nouveau
    previous_id = state.get("last_message_id")
    if previous_id and env("BUMP_DELETE_PREVIOUS", "true").lower() not in ("0", "false", "non", "no"):
        delete_message(webhook, str(previous_id))

    try:
        message_id = send(webhook, content, allowed)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Échec d'envoi : {exc}", file=sys.stderr)
        log_line(f"❌ Rappel bump : échec d'envoi ({exc})")
        return 0  # on réessaiera au prochain passage
    STATE_FILE.write_text(json.dumps({"last_ping": now, "last_message_id": message_id}, indent=2), encoding="utf-8")
    print("Rappel envoyé.")
    log_line(f"🔔 Rappel de bump envoyé · prochain rappel vers <t:{int(now + interval)}:t>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
