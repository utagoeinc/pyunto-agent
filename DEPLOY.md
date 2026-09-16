# Running pyunto-agent serve in a container (production procedure)

These are the server-side steps for enabling "Invite Claude" in the app.
On the same host as pyunto-server, add one agent container to the existing `docker compose` setup.

## Layout

```
Production host
├── pyunto-server/     ← existing (nginx / server / db / redis / certbot)
│   ├── docker-compose.yml
│   ├── docker-compose.agent.yml   ← added (definition of the pyunto-agent service)
│   └── .env                       ← has additions
└── pyunto-agent/      ← added (place this folder next to pyunto-server)
    ├── Dockerfile
    └── persona.md
```

The agent connects to the public API (`https://api.pyunto.com`) as an ordinary client, so it never touches the DB or Redis.
The identity key and the space keys exist only inside the volume `pyunto_agent_data`.

## 1. Put the folder in place

On the production host, place `pyunto-agent` next to `pyunto-server`. All you need are `Dockerfile`, `pyproject.toml`, `persona.md`, `README.md` and `pyunto_agent/*.py` (do not send `.venv` or any keys). The bundled `deploy.sh` rsyncs exactly this list.

```bash
cd pyunto-agent
./deploy.sh user@host:/path/to/pyunto-agent
```

Because `--delete` is used, any extra files at the destination (such as a `.venv` sent there by mistake earlier) will be removed.

## 2. Add to .env (pyunto-server/.env)

```bash
# Make the usual docker compose commands pick up the agent definition as well
COMPOSE_FILE=docker-compose.yml:docker-compose.agent.yml

# server → agent (reached by container name; no port needs publishing)
AGENT_SERVICE_URL=http://pyunto-agent:8788
AGENT_SERVICE_SECRET=<a string generated with openssl rand -hex 32>

# Reply generation for the agent (Anthropic API)
ANTHROPIC_API_KEY=<your Anthropic API key>
PYUNTO_MODEL=claude-sonnet-5
PYUNTO_AGENT_NAME=Claude
```

Both the server and the agent read the same `AGENT_SERVICE_SECRET` value (`docker-compose.agent.yml` passes it through).

## 3. Start it

The procedure is unchanged. Since `COMPOSE_FILE` is set in .env, `docker compose` reads both files.

```bash
cd pyunto-server
./pull_and_run.sh        # git pull → docker compose build → down → up
```

To do it by hand:

```bash
docker compose build pyunto-agent server
docker compose up -d
```

## 4. Check it

```bash
docker compose logs -f pyunto-agent
```

If the following three lines appear, it is running.

```
agent service listening on 0.0.0.0:8788
bridge online as <the agent's user id> (backend=claude-api, dry_run=False)
socket.io connected
```

You can confirm reachability from the server side from inside the server container.

```bash
docker compose exec server sh -c 'wget -qO- --header="X-Agent-Secret: $AGENT_SERVICE_SECRET" http://pyunto-agent:8788/whoami'
```

If `{"user_id": "...", "display_name": "Claude"}` comes back, all is well.

## 5. Try it in the app

1. Open a premium space in Pyunto on the iPhone and tap "Invite Claude" in the top-right menu.
2. Confirm the invitation in the dialog. The agent joins within a few seconds and the app distributes the keys.
3. Post a diary entry and a reply will arrive.
4. Use "Remove Claude" to make it leave.

## Operational notes

- **The first start creates one anonymous account named "Claude".** Deleting the volume changes the identity, and diary entries in spaces it has already joined become unreadable. Always keep `pyunto_agent_data` (it belongs in your backups).
- If you want to carry over an agent you already created on your local Mac (`.agent-data`), copy its contents into the volume and it will keep running under the same account.
- The reply limit is 60 per hour (across all spaces). To raise it, change `Bridge(max_replies_per_hour=…)`.
- Posts that arrive while the agent is stopped get no reply. Picking up what was missed after a restart is left as future work.
- The logs include the first 120 characters of the post body. Do not use `--verbose` in production.
