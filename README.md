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

### 1. Agent — it writes to your users

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

**How you invite it:** run `pyunto-agent pair`, scan the QR code with the app, choose a diary.
The agent starts answering as soon as you approve.

**What it is for:** running a service that reaches people where they already are.

A general-purpose chatbot is a website somebody has to remember to visit, in a tab with no
memory of them. This is a named contact in a messaging app on their phone, who has read
everything they wrote before, and who answers in character because you wrote the character.

That character is a file. `--persona coach.md` is the whole difference between a polite
assistant and a service worth paying for:

```markdown
You are a strength coach. Your client logs every session here.

- Hold them to the programme. If they skipped legs again, say so plainly.
- Always ask for the numbers: weight, sets, reps. A session without numbers is not logged.
- Compare against last week before you praise anything.
- No pep talk. One sentence of encouragement, only when it is earned.
```

```bash
pyunto-agent run --backend claude-api --persona coach.md
```

Some shapes this takes:

| Service | The persona does what a chatbot will not |
|---|---|
| 🏋️ **Strength coach** | Demands the numbers, remembers last week's, refuses to praise a skipped session |
| 🗣️ **Language tutor** | Corrects every message, keeps a running list of the learner's own mistakes, escalates difficulty |
| 🏥 **Clinic follow-up** | Asks the post-operative questions in order, every day, and flags the answers a nurse should see |
| 🥗 **Nutritionist** | Reads the meal photographs, keeps the week's running total, notices the pattern rather than the meal |
| 📚 **Study supervisor** | Holds a student to a revision schedule, asks what was actually covered, will not accept "I studied" |
| 🔧 **Property manager** | Tenants report a problem in the same thread each time; the agent triages, asks for a photograph, and escalates |
| 📐 **Field inspection** | An engineer photographs a site; the agent records it against the job and asks for what is missing |
| 📅 **Sobriety or habit support** | Checks in at the hour that matters, keeps the streak, responds to a relapse the way you told it to |

What makes these work here rather than in a chat window:

* **The persona holds.** It is a file you control, not a prompt the user can talk their way
  out of.
* **It remembers.** `--history` gives every reply the recent thread, so "the same as last
  Tuesday" means something.
* **It is on their phone.** A notification arrives; they reply in a messaging app they already
  have. No login, no tab, no app to learn.
* **You see nothing.** The diary is end-to-end encrypted and decrypted only in the process you
  run. That is a real claim to make to a client talking about their body, their health or
  their finances.
* **One process, many clients.** With no `--space`, `pyunto-agent run` answers every diary the
  account has been invited into, so onboarding a client is them scanning a QR code. Use
  `--space` to pin one agent to one client.

### 2. MCP — you ask Claude about a diary

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
pyunto-agent pair --no-run      # scan the QR code, then it exits
claude mcp add pyunto -- "$(pwd)/.venv/bin/pyunto-agent" mcp
```

**What it is for:** using your diary as memory. Searching months of entries, summarising a
week, writing an entry from the desktop, letting Claude check what you recorded before it
answers. You start every exchange; it never speaks unasked.

### Which one?

| | Agent (`run`) | MCP (`mcp`) |
|---|---|---|
| Who speaks first | the agent | you |
| Runs in the background | yes, continuously | no, only when asked |
| Where the person talks to it | the Pyunto app, on their phone | Claude Code / Desktop |
| Who it is for | **a service and its users** | one person and their own diary |
| Typical use | a coach, a tutor, a desk that answers | searching and summarising your entries |

Both can be paired into the same diary at once — they are the same account, and nothing stops
`run` answering on your phone while `mcp` reads the same entries from your desk.

**A third kind lives elsewhere.** [pyunto-robotics](https://github.com/utagoeinc/pyunto-robotics)
puts a robot at the other end instead of a language model: you write "go and find some
sunlight" and a simulated — or real — machine does it and reports back with photographs. It is
built on this package, and pairs the same way.

## Quick start: a personal trainer your clients message

Building a real service, from nothing to a client's phone.

### 1. Install

```bash
pip install 'pyunto-agent[qr] @ git+https://github.com/utagoeinc/pyunto-agent'
export ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Write the trainer

This file is the service. Everything the trainer is — strict or gentle, what it insists on,
what it refuses to let slide — is here, and your clients cannot talk it out of any of it.

```bash
cat > trainer.md <<'EOF'
You are a strength coach. Each client logs their sessions in this diary.

- Always ask for the numbers: exercise, weight, sets, reps. "I trained today" is not a log --
  ask what they lifted.
- Compare against their recent sessions before responding. If the weight has not moved in
  three weeks, say so.
- If they skipped a session, ask what happened. Once. Then move on.
- No motivational speeches. One line of encouragement, only when the numbers earn it.
- Never give medical advice. Pain goes to a doctor, and say so plainly.
- Reply in the language they wrote in. Two to four sentences.
EOF
```

### 3. Make a QR code to hand out

```bash
pyunto-agent pair --operator "Sano Fitness" --image trainer-qr.png
```

```
Written to trainer-qr.png — send this to whoever should be able to reach the
agent. Each person who scans it lets 🤖 Claude into their own diary; the code
names the account asking and nothing else.
```

Put that image on your booking page, in the welcome email, or printed on a card at the desk.
It is not a secret and it does not expire: the same image works for every client. Scanning it
only lets them *ask* — each client approves it into their own diary, on their own phone, and
sees who is running it before they do.

Use `.svg` instead of `.png` for print, or when Pillow is not installed.

### 4. Start answering

```bash
pyunto-agent run --backend claude-api --persona trainer.md
```

One process serves every client who has scanned the code. A client writes:

> Bench 80kg 5x5, felt heavy on the last set

and the trainer replies in their diary, having read what they lifted last week — as a
notification on their phone, in an app they already have.

Each client's diary is separate and end-to-end encrypted. Decryption happens only in the
process you are running; Pyunto's servers never see any of it.

### Just trying it yourself?

Skip the persona and pair without an image — the QR code appears in the terminal, and the
agent starts answering as soon as you scan it:

```bash
pyunto-agent pair --operator "your name"
```

### Details

Add `--no-run` to draw the QR code and exit, if you would rather start it yourself later with
`pyunto-agent run`. The QR code holds no secret: it names the account asking, and the decision
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

## Agent: Claude API replies

```bash
pyunto-agent run --backend claude-api --persona persona.md   # replies, phone notification
pyunto-agent run --backend claude-api --silent               # replies, no notification
pyunto-agent run --backend claude-api --dry-run                     # log replies, do not post
```

## Agent: Claude Code as the partner

```bash
pyunto-agent run --backend command --command 'claude -p --output-format json'
```

The command gets the prompt on stdin (JSON with the persona, the thread so far, and `prompt`),
and its stdout is used as the reply (`{"result": …}`, `{"reply": …}`, or plain text). Add
`{prompt}` to the command to pass the prompt as an argument instead.

## MCP: Pyunto as tools for Claude

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
