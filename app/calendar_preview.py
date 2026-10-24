"""Start the private read-only Calendar preview; never load the synthetic database."""
import argparse
from pathlib import Path
from secrets import token_urlsafe
from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--private-dir", type=Path, default=Path("data/private-calendar"))
    args = parser.parse_args()
    args.private_dir.mkdir(parents=True,exist_ok=True)
    for name,value in (("calendar.key",Fernet.generate_key()),("session.key",token_urlsafe(64).encode())):
        path = args.private_dir/name
        if not path.exists():
            with path.open("xb") as target:
                target.write(value)
            path.chmod(0o600)
    from app.scheduling.live_host import build_private_host
    from app.main import create_app
    host = build_private_host(database_url=f"sqlite+pysqlite:///{(args.private_dir/'calendar.db').resolve().as_posix()}",
        client_file=args.client_file, encryption_key_file=args.private_dir/'calendar.key',
        session_secret_file=args.private_dir/'session.key',
        redirect_uri=f"http://127.0.0.1:{args.port}/calendar/google/callback")
    import uvicorn
    uvicorn.run(create_app(calendar_host=host),host="127.0.0.1",port=args.port,access_log=False)


if __name__ == "__main__":
    main()
