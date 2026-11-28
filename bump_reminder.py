#!/usr/bin/env python3
"""
Rappel de /bump : poste dans un salon Discord (via webhook) un message qui mentionne un rôle
et contient un lien cliquable vers la commande /bump. C'est TOI qui valides la commande :
le bump reste manuel (l'auto-bump est interdit par Disboard).

Détection du bump (facultative mais recommandée) : avec un vrai compte bot Discord, le script
LIT le salon (lecture seule) pour savoir si Disboard a confirmé un bump. Si quelqu'un a déjà
bumpé, il n'y a pas de rappel inutile, et l'ancien rappel est supprimé.

- Sans dépendance (Python 3.9+).
- À lancer souvent (toutes les 10 minutes environ).

Réglages (variables d'environnement) :
  BUMP_WEBHOOK_URL    webhook du salon (obligatoire)
  BUMP_ROLE_ID        identifiant du rôle à mentionner (facultatif)
  BUMP_COMMAND_ID     identifiant de la commande /bump (facultatif : sinon texte simple "/bump")
  BUMP_INTERVAL_MIN   minutes entre deux rappels si personne ne bumpe (défaut 130)
  BUMP_QUIET_FROM / BUMP_QUIET_TO   heures « calmes » sans rappel, ex. 0 et 8 (facultatif)
  BUMP_TZ             fuseau pour les heures calmes (défaut Europe/Paris)

  Détection (les 3 premières sont nécessaires pour l'activer) :
  DISCORD_BOT_TOKEN   token du compte bot (SECRET)
  BUMP_CHANNEL_ID     identifiant du salon où l'on fait /bump
  DISBOARD_BOT_ID     identifiant de l'utilisateur Disboard
  BUMP_AFTER_BUMP_MIN minutes après un bump avant le rappel suivant (défaut 120 = délai Disboard)
  BUMP_SUCCESS_TEXT   mots (séparés par |) qui prouvent un bump réussi dans le message de Disboard
                      (défaut : "bump effectué|bump done|bump réussi|bump successful")

Options : --test (envoie un message tout de suite) ; --force (poste maintenant) ;
          --debug (affiche ce que le bot lit dans le salon, sans rien poster dans le salon bump).
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
BOT_USER_AGENT = "DiscordBot (https://github.com/kirito7000/Ninja-return-legends, 1.0)"
DEFAULT_SUCCESS_TEXT = "bump effectué|bump done|bump réussi|bump successful"


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


# ---------------------------------------------------------------- détection du bump (lecture seule)

def detection_enabled() -> bool:
    return bool(env("DISCORD_BOT_TOKEN") and env("BUMP_CHANNEL_ID").isdigit() and env("DISBOARD_BOT_ID").isdigit())


def fetch_messages(limit: int = 50) -> list:
    """Lit les derniers messages du salon (les plus récents d'abord). Lève une erreur si Discord refuse."""
    base = env("DISCORD_API_BASE", "https://discord.com/api/v10")
    req = urllib.request.Request(
        f"{base}/channels/{env('BUMP_CHANNEL_ID')}/messages?limit={limit}",
        headers={"Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}", "User-Agent": BOT_USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def snowflake_time(snowflake: str) -> float:
    """Heure (timestamp Unix) d'un identifiant Discord."""
    return ((int(snowflake) >> 22) + 1420070400000) / 1000


def message_text(msg: dict) -> str:
    parts = [msg.get("content") or ""]
    for emb in msg.get("embeds") or []:
        parts += [emb.get("title") or "", emb.get("description") or ""]
    return " ".join(parts).strip()


def interaction_name(msg: dict) -> str:
    return str((msg.get("interaction") or {}).get("name") or "").lower()


def bump_user(msg: dict) -> str:
    user = (msg.get("interaction") or {}).get("user") or (msg.get("interaction_metadata") or {}).get("user") or {}
    return str(user.get("global_name") or user.get("username") or "")


def is_bump_success(msg: dict) -> bool:
    """Vrai si c'est une confirmation de bump de Disboard.

    Les refus (« attends encore X minutes ») sont des messages cachés que seul l'utilisateur voit :
    le bot ne les reçoit jamais. Si le texte est lisible, on exige un des mots de réussite ;
    s'il est vide (intent « Message Content » désactivé), on se rabat sur le nom de la commande.
    """
    if str((msg.get("author") or {}).get("id")) != env("DISBOARD_BOT_ID"):
        return False
    text = message_text(msg).lower()
    if text:
        words = [w.strip().lower() for w in env("BUMP_SUCCESS_TEXT", DEFAULT_SUCCESS_TEXT).split("|") if w.strip()]
        return any(w in text for w in words)
    return interaction_name(msg) == "bump"


def check_bump() -> dict:
    """Retourne {"status": "off"|"error"|"none"|"found", "ts", "user", "error"}."""
    if not detection_enabled():
        return {"status": "off"}
    try:
        messages = fetch_messages()
    except urllib.error.HTTPError as exc:
        hint = {401: "token invalide", 403: "le bot n'a pas accès au salon", 404: "salon introuvable"}.get(exc.code, "")
        return {"status": "error", "error": f"HTTP {exc.code}" + (f" : {hint}" if hint else "")}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}
    for msg in messages:  # du plus récent au plus ancien
        if is_bump_success(msg):
            return {"status": "found", "ts": snowflake_time(msg["id"]), "user": bump_user(msg)}
    return {"status": "none"}


def debug_report() -> int:
    """Affiche ce que le bot lit dans le salon, pour vérifier que la détection est bien calibrée."""
    if not detection_enabled():
        print("Détection non configurée : il manque DISCORD_BOT_TOKEN, BUMP_CHANNEL_ID ou DISBOARD_BOT_ID.")
        return 1
    try:
        messages = fetch_messages(15)
    except urllib.error.HTTPError as exc:
        print(f"Discord a répondu HTTP {exc.code} (401 = token invalide, 403 = pas d'accès au salon, 404 = mauvais salon).")
        log_line(f"🧪 Test de lecture : échec HTTP {exc.code}")
        return 1
    print(f"{len(messages)} messages lus. (texte vide partout = intent « Message Content » désactivé)")
    for msg in messages:
        age = int((time.time() - snowflake_time(msg["id"])) // 60)
        author = msg.get("author") or {}
        mark = "BUMP RÉUSSI" if is_bump_success(msg) else "-"
        print(f"- il y a {age} min | auteur {author.get('id')} ({'bot' if author.get('bot') else 'humain'}) | "
              f"commande « {interaction_name(msg) or '?'} » | texte : {message_text(msg)[:70]!r} | {mark}")
    info = check_bump()
    if info["status"] == "found":
        summary = f"dernier bump détecté il y a {int((time.time() - info['ts']) // 60)} min" + (f" par {info['user']}" if info["user"] else "")
    else:
        summary = "aucun bump détecté parmi les messages lus"
    print("Résultat :", summary)
    log_line(f"🧪 Test de lecture : {len(messages)} messages lus, {summary}")
    return 0


# ---------------------------------------------------------------- état

def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="envoie un message de test, sans toucher à l'état")
    ap.add_argument("--force", action="store_true", help="poste maintenant, quel que soit le délai")
    ap.add_argument("--debug", action="store_true", help="affiche ce que le bot lit dans le salon")
    args = ap.parse_args()

    if args.debug:
        return debug_report()

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
    after_bump = max(60, int(env("BUMP_AFTER_BUMP_MIN") or "120")) * 60
    now = time.time()
    state = read_state()
    last = float(state.get("last_ping", 0))
    changed = False

    # 1) A-t-on déjà bumpé ? (lecture du salon)
    info = check_bump()
    if info["status"] == "error":
        print(f"Lecture du salon impossible : {info['error']}", file=sys.stderr)
        if state.get("read_error") != info["error"]:  # on n'alerte qu'une fois par type d'erreur
            log_line(f"⚠️ Rappel bump : lecture du salon impossible ({info['error']}), rappels selon l'horloge")
            state["read_error"] = info["error"]
            changed = True
    elif state.pop("read_error", None):
        log_line("✅ Rappel bump : la lecture du salon fonctionne de nouveau")
        changed = True

    bump_ts = info.get("ts")
    if bump_ts:
        if bump_ts > last and state.get("last_message_id"):
            # Quelqu'un a bumpé depuis le dernier rappel : on enlève ce rappel devenu inutile
            if env("BUMP_DELETE_PREVIOUS", "true").lower() not in ("0", "false", "non", "no"):
                delete_message(webhook, str(state["last_message_id"]))
            state["last_message_id"] = None
            changed = True
        if state.get("last_bump") != bump_ts:
            state["last_bump"] = bump_ts
            changed = True
            who = f" par {info['user']}" if info.get("user") else ""
            log_line(f"✅ Bump détecté{who} · prochain rappel vers <t:{int(bump_ts + after_bump)}:t>")

    # 2) Quand est le prochain rappel ?
    if bump_ts and last < bump_ts + after_bump:
        next_due = bump_ts + after_bump          # on attend la fin du délai Disboard
    else:
        next_due = last + interval               # personne n'a bumpé : on relance toutes les `interval`

    if not args.force:
        if now < next_due:
            minutes = max(1, int((next_due - now) // 60))
            print(f"Pas encore l'heure (prochain rappel dans environ {minutes} min).")
            log_line(f"🔎 Rappel bump : pas encore l'heure (prochain rappel vers <t:{int(next_due)}:t>)", routine=True)
            if changed:
                write_state(state)
            return 0
        if is_quiet_now():
            print("Heures calmes : pas de rappel.")
            log_line("🌙 Rappel bump : heures calmes, pas de rappel", routine=True)
            if changed:
                write_state(state)
            return 0

    # 3) Rappel : un seul visible à la fois
    previous_id = state.get("last_message_id")
    if previous_id and env("BUMP_DELETE_PREVIOUS", "true").lower() not in ("0", "false", "non", "no"):
        delete_message(webhook, str(previous_id))

    try:
        message_id = send(webhook, content, allowed)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Échec d'envoi : {exc}", file=sys.stderr)
        log_line(f"❌ Rappel bump : échec d'envoi ({exc})")
        if changed:
            write_state(state)
        return 0  # on réessaiera au prochain passage
    state["last_ping"] = now
    state["last_message_id"] = message_id
    write_state(state)
    print("Rappel envoyé.")
    log_line(f"🔔 Rappel de bump envoyé · prochain rappel (si personne ne bumpe) vers <t:{int(now + interval)}:t>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
