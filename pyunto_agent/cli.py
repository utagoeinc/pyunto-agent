"""pyunto-agent command line.

    pyunto-agent whoami
    pyunto-agent join <invite link or code>
    pyunto-agent send <space_id> "text"
    pyunto-agent run --backend claude-api [--persona persona.md] [--space ID] [--dry-run]
    pyunto-agent run --backend command --command 'claude -p --output-format json'
    pyunto-agent mcp

Configuration (env or .env in the working directory; PYUNTO_AGENT_DIR defaults to
~/.pyunto-agent and holds identity.json, space_keys.json, device_id):

    PYUNTO_BASE_URL       default https://api.pyunto.com
    PYUNTO_EMAIL / PYUNTO_PASSWORD     a registered account, or
    PYUNTO_AGENT_NAME     display name for an anonymous agent account (default "Claude")
    ANTHROPIC_API_KEY     for --backend claude-api
    PYUNTO_MODEL          Claude model id (default claude-sonnet-5)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from .auth import AuthError, Session
from .backends import DEFAULT_PERSONA, make_backend
from .bridge import Bridge
from .client import PyuntoClient
from .identity import IdentityStore
from .keys import WrappedSpaceKeyProvider


def _data_dir() -> Path:
    d = Path(os.environ.get("PYUNTO_AGENT_DIR") or Path.home() / ".pyunto-agent")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _device_id(data_dir: Path) -> str:
    p = data_dir / "device_id"
    if p.exists():
        return p.read_text().strip()
    did = str(uuid.uuid4())
    p.write_text(did)
    return did


def _connect() -> tuple[PyuntoClient, WrappedSpaceKeyProvider, IdentityStore]:
    base = os.environ.get("PYUNTO_BASE_URL", "https://api.pyunto.com")
    data_dir = _data_dir()
    email = os.environ.get("PYUNTO_EMAIL")
    password = os.environ.get("PYUNTO_PASSWORD")
    if email and password:
        session = Session(base, email, password)
    else:
        session = Session(
            base,
            device_id=_device_id(data_dir),
            display_name=os.environ.get("PYUNTO_AGENT_NAME", "Claude"),
        )
    identity = IdentityStore(data_dir)
    keys = WrappedSpaceKeyProvider(session, identity, data_dir)
    client = PyuntoClient(session, keys)
    session.login()
    # Idempotent; makes sure members can seal space keys for this identity.
    identity.upload(client)
    return client, keys, identity


def _answer_entries(args, client) -> int:  # noqa: ANN001
    """Listen, and reply to diary entries. Shared by `run` and by `pair` once it is paired."""
    backend = make_backend(args.backend, command=args.command, url=args.url, model=args.model)
    bridge = Bridge(
        client, backend, _persona(args.persona),
        space_ids=set(args.space) if args.space else None,
        history=args.history,
        dry_run=getattr(args, "dry_run", False),
        silent=args.silent,
    )
    try:
        bridge.run()
    except KeyboardInterrupt:
        bridge.stop()
    return 0


def _persona(path: str | None) -> str:
    if path:
        return Path(path).read_text().strip()
    return DEFAULT_PERSONA


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="pyunto-agent")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami")
    p_join = sub.add_parser("join")
    p_join.add_argument("invite")
    p_send = sub.add_parser("send")
    p_send.add_argument("space_id")
    p_send.add_argument("text")
    p_send.add_argument("--thread")
    p_send.add_argument("--silent", action="store_true")
    p_run = sub.add_parser("run")
    p_run.add_argument("--backend", default=os.environ.get("PYUNTO_BACKEND", "claude-api"),
                       choices=["claude-api", "command", "http"])
    p_run.add_argument("--command", default=os.environ.get("PYUNTO_COMMAND"))
    p_run.add_argument("--url", default=os.environ.get("PYUNTO_BACKEND_URL"))
    p_run.add_argument("--model", default=os.environ.get("PYUNTO_MODEL"))
    p_run.add_argument("--persona", default=os.environ.get("PYUNTO_PERSONA"))
    p_run.add_argument("--space", action="append", help="restrict to space id(s)")
    p_run.add_argument("--history", type=int, default=12)
    p_run.add_argument("--silent", action="store_true", help="reply without push notifications")
    p_run.add_argument("--dry-run", action="store_true")
    sub.add_parser("mcp")
    p_pair = sub.add_parser(
        "pair",
        help="show a code for someone to scan with the Pyunto app, letting this agent into a diary",
    )
    p_pair.add_argument("--operator", default="",
                        help="who runs this agent, shown to the person before they approve")
    p_pair.add_argument("--runtime", default="self_hosted",
                        choices=["self_hosted", "hosted", "endpoint"],
                        help="where the diary would be decrypted (default: this machine)")
    p_pair.add_argument("--big", action="store_true",
                        help="draw the QR code larger; use it when a phone will not scan")
    p_pair.add_argument("--image", metavar="FILE",
                        help="also write the QR code to a file (.svg or .png) to send to "
                             "clients; implies --no-run")
    p_pair.add_argument("--no-run", action="store_true",
                        help="draw the QR code and exit, instead of answering once paired")
    p_pair.add_argument("--backend", default=os.environ.get("PYUNTO_BACKEND", "claude-api"),
                        choices=["claude-api", "command", "http"])
    p_pair.add_argument("--command", default=os.environ.get("PYUNTO_COMMAND"))
    p_pair.add_argument("--url", default=os.environ.get("PYUNTO_BACKEND_URL"))
    p_pair.add_argument("--model", default=os.environ.get("PYUNTO_MODEL"))
    p_pair.add_argument("--persona", default=os.environ.get("PYUNTO_PERSONA"))
    p_pair.add_argument("--history", type=int, default=12)
    p_pair.add_argument("--silent", action="store_true",
                        help="reply without push notifications")
    p_serve = sub.add_parser("serve", help="hosted mode: HTTP control API + bridge for all joined spaces")
    p_serve.add_argument("--listen", default=os.environ.get("PYUNTO_AGENT_LISTEN", "127.0.0.1:8788"))
    p_serve.add_argument("--backend", default=os.environ.get("PYUNTO_BACKEND", "claude-api"),
                         choices=["claude-api", "command", "http"])
    p_serve.add_argument("--command", default=os.environ.get("PYUNTO_COMMAND"))
    p_serve.add_argument("--url", default=os.environ.get("PYUNTO_BACKEND_URL"))
    p_serve.add_argument("--model", default=os.environ.get("PYUNTO_MODEL"))
    p_serve.add_argument("--persona", default=os.environ.get("PYUNTO_PERSONA"))
    p_serve.add_argument("--history", type=int, default=int(os.environ.get("PYUNTO_HISTORY", "8")))
    p_serve.add_argument("--silent", action="store_true")

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        client, keys, identity = _connect()
    except AuthError as e:
        print(f"auth failed: {e}", file=sys.stderr)
        return 2

    if args.cmd == "whoami":
        ident = client.session.identity
        print(f"user_id: {client.uuid}\nname: {ident.display_name if ident else '?'}\n"
              f"identity_public_key: {identity.public_key_b64}")
        for s in client.list_spaces():
            sid = str(s.get("uuid"))
            print(f"- {s.get('name')}  {sid}  key={'yes' if keys.has_key(sid) else 'no'}")
        return 0

    if args.cmd == "join":
        sid = client.join(args.invite)
        print(f"joined space {sid}. Ask a member to open it in the app once so the key is shared.")
        return 0

    if args.cmd == "send":
        client.send(args.space_id, args.text, thread_id=args.thread, silent=args.silent)
        print("sent")
        return 0

    if args.cmd == "run":
        return _answer_entries(args, client)

    if args.cmd == "pair":
        from .pairing import (
            encode_payload,
            pairing_payload,
            render_qr,
            save_qr,
            wait_for_scan,
        )

        payload = pairing_payload(
            user_id=client.uuid,
            display_name=client.display_name,
            public_key=identity.public_key_b64,
            operator=args.operator,
            runtime=args.runtime,
        )
        text = encode_payload(payload)

        if args.image:
            # Asked for a file, so do not also fill the terminal with a QR nobody will scan
            # from there. A service hands this to clients: on a booking page, in a welcome
            # email, printed on a card. Waiting at the terminal makes no sense for that, so
            # it exits rather than listening.
            try:
                written = save_qr(text, args.image)
            except RuntimeError as e:
                print(f"\nERROR: {e}")
                return 1
            print()
            print(f"Written to {written} — send this to whoever should be able to reach the")
            print(f"agent. Each person who scans it lets {client.display_name} into their own")
            print("diary; the code names the account asking and nothing else.")
            print()
            print("Then answer all of them at once:")
            print("    pyunto-agent run --backend claude-api --persona your-persona.md")
            return 0

        qr = render_qr(text, big=args.big)
        print()
        if qr:
            print(qr)
        else:
            print("(install the 'qr' extra to draw this as a scannable QR code:")
            print("     pip install 'pyunto-agent[qr]')")
            print()
            print(text)
        print()
        print(f"Scan this in the Pyunto app to let {client.display_name} into a diary.")
        print("The app asks which space, and shows who runs this agent before anything is shared.")
        print("Nothing here is secret: it names the account asking, and the decision stays with")
        print("whoever holds the phone.")
        if args.no_run:
            return 0

        # Wait for the scan, then answer. Drawing a QR code and exiting made the person run a
        # second command, and gave them no way to tell whether the scan had worked -- the
        # square just sat there either way. Scanning is the approval; there is nothing left
        # to decide, so there is no reason to make them come back to the terminal.
        print()
        print("waiting for the scan… (Ctrl-C to stop)")
        space_id = wait_for_scan(client)
        if space_id is None:
            print("Nobody scanned it. Run this again when you are ready.")
            return 1
        print(f"paired — joined a space. Answering entries now.\n")
        args.space = [space_id]
        return _answer_entries(args, client)

    if args.cmd == "mcp":
        from .mcp_server import MCPServer
        MCPServer(client, keys, identity.public_key_b64).serve()
        return 0

    if args.cmd == "serve":
        import threading

        from .roles import RoleAwareBackend, RoleStore, run_checkins
        from .serve import secret_from_env, serve

        backend = make_backend(args.backend, command=args.command, url=args.url, model=args.model)
        persona = _persona(args.persona)

        # Roles make one hosted process serve several issuers. Without them every space gets
        # the same persona, which is fine for a personal agent and wrong for a hosted one:
        # two spaces may belong to different businesses under different agreements.
        roles = RoleStore(_data_dir() / "roles.json")
        backend = RoleAwareBackend(backend, roles, default_persona=persona)

        bridge = Bridge(client, backend, persona, history=args.history, silent=args.silent)

        # Speaking first is the other half. An agent that only answers is a chat window; one
        # that asks on Sunday how the week went is a companion to the record.
        stop = threading.Event()
        threading.Thread(
            target=run_checkins, args=(roles, client, backend, stop),
            daemon=True, name="agent-checkins",
        ).start()

        try:
            serve(bridge, keys, args.listen, secret_from_env(), roles=roles)
        except KeyboardInterrupt:
            bridge.stop()
        finally:
            stop.set()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
