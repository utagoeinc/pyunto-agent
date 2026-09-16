# Making Claude your shared-diary partner (using claude -p)

This guide walks you through inviting Claude into a Pyunto shared diary, so that Claude replies to the entries you write.
Claude runs through a terminal application called "Claude Code". You do not need an API key.
Even if you never normally use the terminal, you can copy and run each step in order and be finished in about 15 minutes.

> Intended for: macOS (both Apple silicon and Intel). The same flow works on Windows, although some commands differ (activating the venv).

---

## 0. What you need

| Item | What it is for |
|---|---|
| A Claude account (Pro or Max plan) | Signing in to Claude Code |
| A computer (Mac) | The machine that keeps Claude running. Replies stop while it is asleep |
| The Pyunto app on iPhone / Android | The side that writes the diary |
| Node.js 18 or later, Python 3.11 or later | Needed to run Claude Code and the bridge program respectively (installed in step 1) |

---

## 1. Install Claude Code and sign in

Open the terminal (on macOS: Applications → Utilities → Terminal).

Check whether Node.js is installed.

```bash
node -v
```

If it prints `v18` or higher, you are fine. If not, install the LTS release from <https://nodejs.org/en> and open a fresh terminal window.

Install Claude Code.

```bash
npm install -g @anthropic-ai/claude-code
```

Sign in. A browser window opens; approve it with your Claude account.

```bash
claude
```

Once the screen appears, type `/login` and press Enter, approve in the browser, then close it with `/exit`.

Check that it works. If the single line below comes back with a short reply, you are ready.

```bash
claude -p "Hello. Introduce yourself in one line."
```

---

## 2. Install the bridge program (pyunto-agent)

Check whether Python is installed.

```bash
python3 --version
```

If it prints `3.11` or higher, you are fine. If not, install it from <https://www.python.org/downloads/>.

Move to wherever you want pyunto-agent to live and fetch it (unzip the archive or use git clone, depending on how it was supplied).

```bash
cd ~
git clone https://github.com/pyunto/pyunto-agent
cd pyunto-agent
```

Create a virtual environment and install into it.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Check that it works. The first time you run it, a dedicated agent account of your own (display name Claude) is created automatically.

```bash
.venv/bin/pyunto-agent whoami
```

If `user_id:` and `identity_public_key:` are shown, all is well. If you would like a different display name, set it beforehand like this.

```bash
PYUNTO_AGENT_NAME="Claude" .venv/bin/pyunto-agent whoami
```

---

## 3. Create an invitation link in the Pyunto app

1. Open the Pyunto app. To start a brand-new shared diary just for Claude, choose "New shared diary (for two)" from the talk list. To add Claude to an existing shared space, open that space and choose "Add a friend to this space" from the menu (the three lines at the top right). You cannot invite anyone into your own private "Records" space.
2. On the invitation screen, tap "Copy link". A link of the form `https://api.pyunto.com/invite/…` or `pyunto://invite/…` is copied.
3. Send that link to your Mac (by AirDrop, a note, an email to yourself, and so on).

---

## 4. Add Claude to the space

In the terminal, paste in the link you copied and run it (wrapped in quotes).

```bash
.venv/bin/pyunto-agent join 'https://api.pyunto.com/invite/paste-the-link-here'
```

When `joined space …` appears, Claude has joined.

**At this point, open that space once on your iPhone.** The app then hands the new member (Claude) the key needed to read the diary. Simply opening it is enough.

Check the result.

```bash
.venv/bin/pyunto-agent whoami
```

If the line for the space says `key=yes`, Claude can read the diary. If it still says `key=no`, open the space on your iPhone again and then check once more.

---

## 5. Start Claude

```bash
.venv/bin/pyunto-agent run --backend command --command 'claude -p --output-format json' --persona persona.md
```

Once `bridge online` and `socket.io connected` appear, it is waiting for entries. Leave this terminal window open.

---

## 6. Write a diary entry

In Pyunto on your iPhone, post one diary entry to that space. Claude's reply arrives in the same thread within a few seconds to a few tens of seconds.

- If you add more to the same thread, Claude reads the flow of that thread (the 12 most recent entries) before replying.
- If you start a new thread, Claude treats it as a new subject.
- Claude does not reply to posts that contain only a photo. Add some words and it will reply.

---

## 7. Stopping and restarting

- To stop: press `Ctrl + C` in the terminal from step 5.
- To restart: run the step 5 command again. You do not need to redo the joining or the key setup.
- It stops when the Mac goes to sleep. To run it for long stretches, turn off sleep under "System Settings → Battery / Energy Saver".

---

## Common stumbling blocks

| Symptom | What to do |
|---|---|
| `claude: command not found` | You did not open a fresh terminal after installing Node.js. Open a new one and run `npm install -g @anthropic-ai/claude-code` again |
| `claude -p` returns nothing, or asks you to sign in | Start `claude` and go through `/login` again |
| Still says `key=no` | Open the space on your iPhone. It has to be opened by the person (member) who sent the invitation |
| No reply arrives after posting | Look at the log in the terminal. `skipping entry` means the key has not been handed over yet; `command exited` means an error on Claude's side (a usage limit, for instance) |
| Replies are slow | `claude -p` starts up afresh for each entry, so it takes 3–10 seconds. That is expected |
| You want to change the tone of the replies | Edit `persona.md` and run step 5 again |

---

## Things worth knowing

- The text of your diary is sent to Claude (Anthropic) in order to compose the replies. Do tell the person you share the diary with.
- Claude accounts have usage limits. Once a limit is reached, replies stop, and resume after a while.
- Claude can only read the space you invited it to. It cannot read your "Records" space or any other space.
- The agent's keys are stored in `~/.pyunto-agent/`. If you delete that folder, Claude can no longer read the diary (and you will have to start again from the invitation).
