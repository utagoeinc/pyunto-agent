# pyunto-agent

Make an external agent the partner of a Pyunto exchange diary. The agent is an ordinary member:
its own Pyunto account, its own X25519 identity key, invited with the normal invite link. The
server is unchanged and never sees plaintext; decryption happens in this process.

Two ways to use it, same process:

* **Push** — `pyunto-agent run` listens for entries and replies through a backend
  (Claude API, Claude Code via `command`, or any HTTP endpoint).
* **Pull (MCP)** — `pyunto-agent mcp` exposes the diary as MCP tools for Claude Code / Claude
  Desktop / any MCP client.

## Setup

```bash
git clone https://github.com/utagoeinc/pyunto-agent
cd pyunto-agent
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env      # fill in ANTHROPIC_API_KEY (and optionally an account)
```

Add `'.[qr]'` if you want `pyunto-agent pair` to draw a scannable square in the terminal.

Without `PYUNTO_EMAIL`, the agent uses an anonymous account named `PYUNTO_AGENT_NAME`
(default "Claude"); the device id and identity key are kept in `~/.pyunto-agent/`.

## Pair with a diary

Either direction works; pick whichever end you are standing at.

**From the terminal** (you are already running the agent):

```bash
pyunto-agent pair --operator "your name or company"
```

It prints a code. Scan it in the Pyunto app, choose the diary, and confirm. The code holds no
secret — it names the account asking, and the decision stays with whoever holds the phone.

**From the app** (someone else set the agent up for you):

1. In the Pyunto app, create or open a shared space and generate an invite link.
2. `pyunto-agent join 'pyunto://invite/…'`
3. Open the space once in the app. The app sees the new member and shares the space key with
   the agent's identity key (this is the E2EE key distribution the apps already do).
4. `pyunto-agent whoami` should now show `key=yes` for that space.

## Push: Claude API replies

```bash
pyunto-agent run --backend claude-api --persona persona.md          # replies with push
pyunto-agent run --backend claude-api --silent                      # replies without push
pyunto-agent run --backend claude-api --dry-run                     # log replies, do not post
```

## Push: Claude Code as the partner

```bash
pyunto-agent run --backend command --command 'claude -p --output-format json'
```

The command gets the prompt on stdin (JSON with the persona, the thread so far, and `prompt`),
and its stdout is used as the reply (`{"result": …}`, `{"reply": …}`, or plain text). Add
`{prompt}` to the command to pass the prompt as an argument instead.

## Pull: Pyunto as MCP tools

```bash
claude mcp add pyunto -- "$(pwd)/.venv/bin/pyunto-agent" mcp
```

Run that from the clone, or substitute the absolute path to the `pyunto-agent` executable —
Claude Code launches it without a shell, so a bare `pyunto-agent` only works if it is on the
PATH of the process that starts Claude Code.

Tools: `whoami`, `list_spaces`, `list_members`, `list_threads`, `read_thread`,
`wait_for_message`, `post_entry`, `react`, `post_sticker`, `post_list_item`,
`quick_list_stats`, `join_space`. A minimal autonomous loop in Claude Code:

```
> Use wait_for_message, then reply with post_entry in the same thread. Repeat.
```

Two of these are why a diary partner can say things a chat model cannot.

`quick_list_stats` counts the repeated things a diary tracks — medicines taken, books read to a
child, meals, walks — over a period. It is what lets an agent say "that is the third time this
week" instead of asking. The tally is assembled on your machine from decrypted entries: the
server stores these posts as ciphertext, so no endpoint could answer it.

`list_members` says who else is in the space, and for each agent who runs it and where it runs.
Call it before writing anything sensitive — it tells you who reads what you post.

Pair it with `@pyunto/tm-mcp` and the partner can also read and book the human's schedule.

## Notes

* Diary text is sent to the backend you choose. With `claude-api` that is Anthropic's API; say so
  to the people in the diary.
* Rate limit: 60 replies per hour by default (`Bridge(max_replies_per_hour=…)`).
* Spaces created before August 2026 still expose a legacy raw key; newer ones require the
  wrapped key, which is why step 3 above matters.
* Tests: `.venv/bin/pytest` (includes the sealed-box fixture from `E2EE_IMPLEMENTATION.md`).

## Licence

Apache-2.0. See [LICENSE](LICENSE).
