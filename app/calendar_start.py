"""Local team Google launcher; keep credentials and real users out of the demo."""
import argparse
import json
import os
from pathlib import Path
import socket
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def port_available(port):
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def resolve_client(explicit, private_dir):
    configured = explicit or os.getenv("STUDYMATE_GOOGLE_CLIENT_FILE")
    if configured:
        return Path(configured).expanduser()
    saved = private_dir / "launch-client.json"
    if saved.exists():
        try:
            return Path(json.loads(saved.read_text(encoding="utf-8"))["client_file"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
    candidate = ROOT / "secrets" / "google-web-client.json"
    return candidate if candidate.is_file() else None


def choose_client():
    try:
        from tkinter import Tk, filedialog
        window = Tk()
        window.withdraw()
        try:
            selected = filedialog.askopenfilename(title="Select private Google Web OAuth JSON",
                filetypes=[("OAuth JSON", "*.json")])
            return Path(selected) if selected else None
        finally:
            window.destroy()
    except Exception:
        return None  # Headless Python can use --client-file or the environment.


def inspect_setup(client_file, port, private_dir):
    """Local checks only: remote Google test-user access is not verified here."""
    callback = f"http://127.0.0.1:{port}/calendar/google/callback"
    result = {"ready":False, "mode":"google-readonly", "callback":callback, "issues":[],
        "remote_google_configuration":"not_verified"}
    def issue(code, message):
        result["issues"].append({"code":code, "message":message})
    if not 1 <= port <= 65535:
        issue("INVALID_PORT", "Choose a port from 1 to 65535.")
    if client_file is None or not client_file.is_file():
        issue("CLIENT_FILE_REQUIRED", "Obtain the team's Web OAuth JSON privately and select it in Start-Calendar.cmd. Do not commit it to Git.")
    else:
        try:
            raw = json.loads(client_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("web"), dict):
                issue("WEB_CLIENT_REQUIRED", "Use a Web application OAuth JSON, not a Desktop client or API key.")
            elif callback not in raw["web"].get("redirect_uris", []):
                issue("CALLBACK_MISMATCH", "Register the exact callback shown here in Google Cloud, then download updated JSON. localhost and 127.0.0.1 are different.")
            else:
                from .scheduling.google_config import GoogleConfig
                GoogleConfig.from_file(client_file, callback)
        except (OSError, ValueError, KeyError, TypeError):
            issue("CLIENT_FILE_INVALID", "OAuth JSON is unreadable or incomplete. Select a valid Web client file; do not paste its contents.")
    if (private_dir / "calendar.db").exists() and any(
        not (private_dir / name).is_file() for name in ("calendar.key", "session.key")
    ):
        issue("RUNTIME_KEYS_MISSING", "Restore the existing database's original keys. Do not regenerate them or reuse the synthetic database.")
    if 1 <= port <= 65535 and not port_available(port):
        issue("PORT_IN_USE", "Close the service on this port, or choose another callback port already registered in Google Cloud.")
    result["ready"] = not result["issues"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path)
    parser.add_argument("--private-dir", type=Path,
        default=Path(os.getenv("STUDYMATE_CALENDAR_PRIVATE_DIR", ROOT / "data/private-calendar")))
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--check", action="store_true", help="Safe diagnostics; no server or Google request")
    parser.add_argument("--choose-client", action="store_true", help="Select missing client JSON with a local file picker")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    client_file = resolve_client(args.client_file, args.private_dir)
    if not args.check and args.choose_client and (client_file is None or not client_file.is_file()):
        client_file = choose_client()
    result = inspect_setup(client_file, args.port, args.private_dir)
    if args.check or not result["ready"]:
        print(json.dumps(result, indent=2))
    if not result["ready"]:
        if not args.check:
            print("Setup help: docs/calendar-google-user-connect.md")
        return 2
    if args.check:
        return 0
    args.private_dir.mkdir(parents=True, exist_ok=True)
    (args.private_dir / "launch-client.json").write_text(json.dumps({"client_file":str(client_file.resolve())}), encoding="utf-8")
    print(f"Private Google read-only preview: http://127.0.0.1:{args.port}/login")
    print("Google prerequisites: Calendar API enabled, exact Web callback, and test-user access while the app is in Testing.")
    print("Create a private local account, then consent in Google. Team Matching/Session adapters are separate.")
    timer = None
    if args.open_browser:
        timer = threading.Timer(1, webbrowser.open, args=[f"http://127.0.0.1:{args.port}/login"])
        timer.daemon = True
        timer.start()
    try:
        from .calendar_preview import main as preview
        preview(["--client-file", str(client_file), "--port", str(args.port), "--private-dir", str(args.private_dir)])
    except (OSError, ValueError):
        print("Private preview could not start. Keep existing data/keys; check port, OAuth JSON and runtime folder. See the setup guide.")
        return 2
    finally:
        if timer:
            timer.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
