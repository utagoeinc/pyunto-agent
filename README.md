# pyunto-agent

Make an external agent the partner of a Pyunto exchange diary. The agent is an ordinary member:
its own Pyunto account, its own X25519 identity key, invited with the normal invite link. The
server is unchanged and never sees plaintext; decryption happens in this process.

[**Pyunto for iPhone and iPad**](https://apps.apple.com/app/id6755097890) ·
[**Pyunto for Android**](https://play.google.com/store/apps/details?id=com.pyunto.app) ·
[**pyunto-robotics**](https://github.com/utagoeinc/pyunto-robotics) — the same idea, with a
robot at the other end

---

## Two kinds of agent

The same package, the same account, the same keys. What differs is **who starts the
conversation**.

### 1. Push — the agent answers your diary

You write; it replies, unprompted, in the same thread.

```
     your phone                     your computer
 ┌───────────────┐            ┌──────────────────────┐
 │  Pyunto app   │            │  pyunto-agent run    │
 │               │            │                      │
 │  "tough day   │  ───────▶  │  reads the entry     │
 │   at work"    │  encrypted │  decrypts it HERE    │
 │               │            │        │             │
 │               │            │        ▼             │
 │  "that sounds │  ◀───────  │  Claude API, or      │
 │   exhausting" │  encrypted │  Claude Code, or     │
 └───────────────┘            │  your own HTTP URL   │
                              └──────────────────────┘
        │                                │
        └────────  api.pyunto.com  ──────┘
              (ciphertext only, never plaintext)
```

**How you invite it:** run `pyunto-agent pair`, scan the square with the app, choose a diary.
The agent starts answering as soon as you approve.

**What it is for:** an exchange diary with something that always writes back. A partner for
daily entries, a reflective prompt at the end of the day, a second voice in a shared space.
It runs continuously and speaks on its own.

### 2. Pull (MCP) — you ask Claude about your diary

Nothing runs in the background. Claude Code or Claude Desktop reaches into the diary when you
ask it to.

```
     your computer
 ┌────────────────────────┐
 │  Claude Code / Desktop │
 │            │           │        ┌──────────────────┐
 │            ▼           │        │  your phone      │
 │  "what did I write     │        │  Pyunto app      │
 │   about the garden?"   │        │                  │
 │            │           │        │  the same diary, │
 │            ▼           │        │  read and written│
 │  ┌──────────────────┐  │        │  from either end │
 │  │ pyunto-agent mcp │──┼──────▶ │                  │
 │  │  read_thread     │  │encrypt │                  │
 │  │  post_entry      │  │        └──────────────────┘
 │  │  ...12 tools     │  │
 │  └──────────────────┘  │
 └────────────────────────┘
```

**How you invite it:** pair it the same way, but with `--no-run` — this one should not sit
there answering — then register it with your MCP client once:

```bash
pyunto-agent pair --no-run      # scan the square, then it exits
claude mcp add pyunto -- "$(pwd)/.venv/bin/pyunto-agent" mcp
```

**What it is for:** using your diary as memory. Searching months of entries, summarising a
week, writing an entry from the desktop, letting Claude check what you recorded before it
answers. You start every exchange; it never speaks unasked.

### Which one?

| | Push (`run`) | Pull (`mcp`) |
|---|---|---|
| Who speaks first | the agent | you |
| Runs in the background | yes, continuously | no, only when asked |
| Where you talk to it | the Pyunto app | Claude Code / Desktop |
| Typical use | a diary partner that replies | your diary as searchable memory |

Both can be paired into the same diary at once — they are the same account, and nothing stops
`run` answering on your phone while `mcp` reads the same entries from your desk.

**A third kind lives elsewhere.** [pyunto-robotics](https://github.com/utagoeinc/pyunto-robotics)
puts a robot at the other end instead of a language model: you write "go and find some
sunlight" and a simulated — or real — machine does it and reports back with photographs. It is
built on this package, and pairs the same way.

## Quick start

Four commands, and the agent is answering your diary.

```bash
git clone https://github.com/utagoeinc/pyunto-agent
cd pyunto-agent
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,qr]'
cp .env.example .env                       # put your ANTHROPIC_API_KEY in it

.venv/bin/pyunto-agent pair --operator "your name"
```

A square appears in the terminal. Scan it with the Pyunto app, choose which diary to let the
agent into, and approve — **the agent then starts answering by itself.** No second command.

```
waiting for the scan… (Ctrl-C to stop)
paired — joined a space. Answering entries now.
```

Write an entry in that diary and the agent replies in the same thread.

Open the space once in the app after approving. The diary is end-to-end encrypted, so a member
hands the agent a key; the server cannot.

### Details

Add `--no-run` to draw the square and exit, if you would rather start it yourself later with
`pyunto-agent run`. The square holds no secret: it names the account asking, and the decision
stays with whoever holds the phone.

Without `PYUNTO_EMAIL` the agent uses an anonymous account named `PYUNTO_AGENT_NAME` (default
"Claude"); the device id and identity key live in `~/.pyunto-agent/`.

### Installing without cloning

If you only want to run the agent, not work on it — one line, no clone:

```bash
pip install 'pyunto-agent[qr] @ git+https://github.com/utagoeinc/pyunto-agent'
pyunto-agent pair --operator "your name"
```

Neither package is on PyPI yet, which is why the install comes from git.

### Longer guides

* [GUIDE.md](GUIDE.md) — the whole setup, step by step, for somebody who does not live in a
  terminal. Uses Claude Code (`claude -p`) rather than an API key.
* [DEPLOY.md](DEPLOY.md) — running `pyunto-agent serve` in a container alongside the server.

### Pairing from the app instead

If someone else set the agent up for you, go the other way:

1. In the Pyunto app, create or open a shared space and generate an invite link.
2. `pyunto-agent join 'pyunto://invite/…'`
3. Open the space once in the app, so the space key is shared with the agent's identity key.
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
