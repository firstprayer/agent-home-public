# Agent Home

**Talk to your Codex agent, anywhere.**

[Download for macOS](https://github.com/firstprayer/agent-home-public/releases/tag/v0.3.2) ·
[Project website](https://firstprayer.github.io/agent-home-public/) ·
[Get started](#run-and-configure)

A free, open-source app that connects Telegram to Codex running on your computer.
Your computer does the work. Your conversation goes with you.

![Agent Home desktop with a fictional example workspace](docs/images/workspace.png)

*Example workspace. Your own agents and sessions appear here.*

- **Continue the same session.** Send a message from your phone to the exact Codex
  root session you use on your computer, or steer work already in progress.
- **Bring more than words.** Send photos, videos, and voice notes. Images reach
  Codex as image inputs; video and voice files use the agent's available local tools.
- **Give every project a home.** Keep durable agent identities, independent
  Telegram connections, and replaceable runtime sleeves in one overview.
- **Stay in control.** Explicit sender access, local SQLite storage, visible
  connection status, and recovery of saved managed sessions.

The native macOS app includes a **Get started** guide that opens each setup step:
create an identity, assign a Codex sleeve, connect a Telegram bot, and explicitly
allow your own Telegram account in **External Identities**. Once the connections
are ready, open your bot directly from the app and send your first message.

Agent Home is free under the [GNU AGPLv3 license](LICENSE). Bring your own Codex account
and Telegram bot; any charges from your configured model service remain separate.
The host computer must stay awake, online, and running Agent Home. Telegram and
your configured Codex provider handle their respective communications.

**Preview status:** macOS desktop first; the Python server supports macOS and
Linux. Windows support is planned. Current DMGs are ad-hoc signed and are not
Apple notarized. The v0.3.2 preview download supports Apple Silicon Macs on macOS
15 or later. Intel users can build from source; Windows is not supported yet.

Agent identities have stable UUIDs; their runtime sleeves can be assigned,
replaced, or restored. Only confirmed Codex root sessions are supported, and at
most one identity may hold the built-in AdminAppSleeve. Sender access is managed
across all identities through the top-level External Identities page. Application
storage and allowed sleeve folders live under Settings.

## Run and configure

### macOS app

Download the [v0.3.2 macOS preview](https://github.com/firstprayer/agent-home-public/releases/tag/v0.3.2).
Choose `Agent-Home-0.3.2-macOS-arm64.dmg` for an Apple Silicon Mac. The release
includes `SHA256SUMS` and the corresponding source code.

Open the DMG, drag **Agent Home** into **Applications**, then open it. A native
window displays the existing Vue dashboard; there is no localhost address to
enter and no Python, uv, Node, or speech model to install for the bridge. Codex
must still be installed and running separately for Codex sleeves.

Preview builds are not Apple notarized. If macOS blocks a downloaded preview,
review its source and release first, then use **System Settings → Privacy &
Security → Open Anyway** if you trust it. See [Apple's instructions](https://support.apple.com/en-us/102445).

Closing the window leaves the service running in the menu bar. Reopen the app or
choose **Show Agent Home** to return. **Quit Agent Home** stops its own bridge
process gracefully; it does not stop Codex. The service also exits if its launcher
disappears. Startup failures show an error with access to the log rather than a
blank window. The app does not start automatically at login.

The desktop app creates `~/.config/agent-communication/service.toml` on first
launch and respects an existing file, including its configured database and port.
**Agent Home → Open Service Configuration…** opens that file; restart the app
after editing it. The database stays outside the application, so replacing the
app preserves identities, policies, tokens, and delivery checkpoints. Logs live
at `~/Library/Logs/Agent Home/server.log`. Stop an existing terminal-launched
bridge before starting the desktop app; the database still has one writer.

For normal operation and upgrades, prefer `/Applications/Agent Home.app`.
Develop against a disposable database on a separate port; deploy
changes by rebuilding and replacing the installed app, then verify its native
window and restored agents/listeners. Keep the existing service configuration and
database outside the bundle, and let Codex keep running during upgrades.

Build on a Mac with Xcode Command Line Tools and `uv`:

```sh
uv run --managed-python --group macos-build python packaging/macos/build.py
```

The build uses the committed Vue assets, freezes Python and its locked runtime
dependencies, compiles an AppKit/WKWebView launcher, and creates
`dist/Agent-Home-0.3.2-macOS-arm64.dmg` (architecture follows the build machine).
Only explicit application assets enter the bundle, never user data or secrets.
The native launcher targets macOS 15.0, and the build rejects bundled binaries
that require a newer system. Use uv’s managed Python to avoid linking against
newer host-only libraries. Verify release candidates on macOS 15 as well as the
build host. Current local builds are ad-hoc signed and are **not Apple notarized**.
Developer ID signing and notarization improve installation without requiring
users to approve an unidentified developer. Voice decoding remains the assigned agent's job.

### Command line

Requires macOS or Linux, Python 3.11+, and `uv`. Existing Codex sleeves connect to
the Codex shared app-server daemon. Explicit creation requires an installed,
configured Codex CLI and starts its shared daemon if it is not already running.

```sh
uv sync --locked
uv run python bridge.py
```

Open <http://127.0.0.1:8765/>. The dashboard remains usable when Codex is offline.
There is no dashboard login; local programs on your machine can manage the bridge.
The default database is `~/.config/agent-communication/agent-home.sqlite3`.

For explicit service configuration, copy [service.example.toml](service.example.toml)
to the ignored `service.toml` and run:

```sh
uv run python bridge.py --config service.toml
```

```toml
# ServiceConfig: reject unknown keys and invalid values at startup.
db_path = "~/.config/agent-communication/agent-home.sqlite3"
port = 8765
sleeve_allowed_folders = []
```

Relative database paths resolve against the configuration file’s directory. CLI
`--db-path /private/path/home.sqlite3` and `--port 8766` override configuration.
The older `--state-dir /private/path` flag selects `agent-home.sqlite3` in that
directory. The service holds process locks for its database and legacy state
directory. Stop an older bridge before upgrading; it must not write JSON state
while the new bridge migrates it. Stop a foreground server with Ctrl-C.

Service TOML maps to a frozen `ServiceConfig` dataclass. The repository also
requires **every YAML file used by Python to map to a dedicated `@dataclass`**,
with validation at the load boundary; see [AGENTS.md](AGENTS.md).

## Application Settings and new sleeves

Open **Settings** to change the database path and allowed sleeve folders. Settings
are stored in the service TOML outside the database and take effect immediately.
The port is displayed as service information; change it in the config file and
restart the app. Saving Settings requires startup with a config file (the desktop
app always has one).

Changing **Database path** moves the current database working set to a new file,
including all identities, tokens, policies, queues, checkpoints, and launch
receipts. Destination process locks are acquired before copying. Existing files
are never overwritten. The service config commits atomically before subsequent
writes switch to the new database. The old private database is retained as a
backup. A failed copy or config commit keeps the original active. Do not start
another service from a retained old database. Future identity-level backup
settings belong on this application Settings page; identity backup/restore is
not implemented here.

**Allowed working folders** accepts one directory per line. The list starts empty,
which disables creation. A configured directory and its subdirectories are
allowed. The target must already exist. Actual filesystem locations are checked,
so a sibling with the same prefix or a symlink pointing outside an allowed root
cannot bypass the policy. Removing a directory prevents future launches there;
it does not stop or reconfigure existing sleeves.

In an identity's **Assign / Change sleeve** dialog, choose **New Codex sleeve**,
enter the working folder, and click **Create sleeve**. The service creates an
exact root thread with never-ask approvals and full access, then starts
`codex resume --yolo --remote unix://<control socket> --cd <folder> <thread UUID>`
in its own detached PTY. Before starting that CLI, creation sends one short
initialization message asking for `Ready` without file inspection or tool use,
and confirms the exact thread can resume. The returned root is
preselected; click **Assign sleeve** to connect the identity. Creating a sleeve
does not bypass the existing rule against replacing a working sleeve.

Creation runs in the background and does not open a Terminal window. The one-time
initialization materializes the history Codex needs to reconnect; it is never
appended to subsequent Telegram messages or sent again on restart. Open the same
sleeve interactively with its full address:

```sh
codex resume --yolo --remote unix://<control socket> --cd <folder> <full thread UUID>
```

An incomplete UUID, such as its final eight characters, is not a sleeve address.

The detached PTY keeper and Codex process survive Agent Home exit/restart or
bundle replacement. App shutdown, reassignment, and settings changes do not kill
them. The shared Codex daemon owns the thread; the bridge reconnects by its exact
socket and thread UUID. If the CLI exits independently, the existing thread may
remain on the daemon. Agent Home never automatically respawns a failed launch.
`--yolo` disables approvals and sandboxing; the folder list limits launch
locations rather than later filesystem access.

Creation uses a persisted request UUID for retries. An uncertain RPC/launch is
not replayed, including after restart; check available roots and the private
`sleeve-processes/<thread UUID>.log` under the database directory before creating
another. Folder and root validation happen before attaching anything. Fresh
threads with no first user message are supported: Codex's specific
"not materialized yet" history response is treated as an empty history page.
Other runtime errors continue to be reported.

For an assigned sleeve created by Agent Home, open **Change sleeve → Restore
current sleeve** to reconnect the saved session and reopen a stopped CLI. The
dialog displays the full saved UUID. Session status and **CLI running/stopped**
are separate: closing a CLI does not necessarily stop work on the shared daemon.
Recovery starts the default local daemon if it is unavailable, validates the same
root and folder, and resumes that exact UUID. It sends no initialization or user
task and never replaces the identity, binding, or delivery checkpoints. The
current allowed-folder policy applies when launching a replacement CLI. Changed
permissions, missing history, and unavailable folders are reported for manual
review. A live CLI is reused; per-sleeve keeper locks prevent duplicate managed
CLIs even after Agent Home restarts. Recovery is an explicit button, not an
automatic process restart or login-startup service.

Each identity stores one current sleeve binding. All sleeve creation receipts
are retained in SQLite `runtime_state.sleeve_launches`, including older created
session IDs. Reassignment does not delete those receipts. This is not a complete
identity assignment history: old externally created bindings are not archived.
Conversation contents remain in Codex's own persistent history; a saved UUID
alone cannot recover history that was deleted from Codex.

## Communication and access

Telegram is currently the supported communication provider. Sleeves can use
Codex root agents or the built-in AdminAppSleeve. Connections can be added, replaced, or disabled without
changing the agent’s UUID, sleeve, or access policy.

Paste the BotFather token directly into the communication dialog. It is stored
in the private local SQLite database and verified with Telegram `getMe`. When
editing, leave the token blank to retain the existing credential. Tokens are
never returned by the management API or included in logs. Keep them out of Git.
A bot may belong to only one identity.

`bot_id` is Telegram’s permanent numeric identifier, returned by `getMe`. The
server uses it to recognize a bot independently of its username, prevent duplicate
registration, and identify incoming receipts. It is read-only and is not a credential.

Access belongs to `ExternalIdentity`, independently of agent identities and bot
connections. Each row on **External Identities** represents one external account,
with a persistent UUID, provider, Telegram username and/or numeric user ID, and a
`communication_policy`:

| Policy | Access |
|---|---|
| Always allowed (`always_allowed`) | All current and future agent identities |
| Always blocked (`always_blocked`) | No agent identities |
| Specific agents (`specific_agents`) | Only the listed agent UUIDs; an empty selection rejects all messages |

Incoming messages must first match a registered external identity. An unknown
sender is rejected; a recognized sender follows that identity's policy. There
are no per-agent access lists or automatic registration. New external identities
default to **Always blocked** until a policy is explicitly saved. For example:

```json
{
  "provider": "telegram",
  "user_id": "123456789",
  "username": "@your_username",
  "communication_policy": {"mode": "specific_agents", "agent_ids": ["<agent UUID>"]}
}
```

User IDs are positive numeric strings. Usernames are normalized. A username-only
registration binds to the numeric ID from its first real Telegram sender metadata;
when a recent unregistered sender is registered, that metadata can establish the
ID immediately. Once known, only the numeric account ID matches, including after
account renames; a recycled username cannot inherit access. Numeric registrations
take precedence over unbound username registrations. Message text cannot claim
another sender's identity. Registered account identifiers cannot be edited through
the policy form; edit the policy to block an account or register a different one.

Unknown private senders appear on **External Identities → Unregistered senders**
for 30 minutes. Registration prefills their account details and defaults to
blocked. Rejected messages are never replayed. Groups and mismatched private-chat
sender IDs are ignored. Access is checked on receipt, before submission, on
outgoing delivery, and for approval callbacks. A policy change drops newly
unauthorized queued input and replies across all agents; work already submitted
and Telegram deliveries already in flight cannot be recalled.

Replacing or disabling a bot clears its old queued input, replies, pending senders,
turn recipients, and approval buttons; uncertain receipts remain available for
review. Rotating the token for the same bot preserves its offset, verified sender
IDs, and connected chats. The token is never returned by the API.

## AdminAppSleeve

Assign the **AdminAppSleeve** sleeve type to an identity, then connect that identity
to a Telegram bot in **Settings**. The bot token stays on the identity. No Codex
process, socket, or conversation ID is required. Only one identity can hold this
sleeve, including across concurrent assignments and restarts. Detach it before
assigning it elsewhere. Replacing an active Codex sleeve still requires stopping
its turn first.

The sleeve accepts only these fixed commands:

| Command | Behavior |
|---|---|
| `/menu`, `/start` | Open a paginated menu of accessible agents and actions |
| `/help` | List commands and availability limits |
| `/cancel` | Cancel a name/token input step and return to the menu |
| `/list` | List every accessible agent, current cached status, and its assigned sleeve or explicit absence of a sleeve |
| `/create <agent name>` | Create an identity and offer buttons to select an existing Codex root sleeve |
| `/bind <agent name>` | Offer sleeve-selection buttons for an existing identity |
| `/kill <agent name>` | Cancel queued instructions, request cancellation of in-flight submissions, and interrupt an observed active root Codex turn |

Names match exactly, including spaces: `/kill Research partner`. Commands require
ExternalIdentity access to both the admin identity and the target identity.
Lists include only accessible identities. Replies go only to
the requesting private chat. Unknown or arbitrary instructions return help; they
never execute as shell commands or model prompts.

Creating identities and assigning sleeves require an external identity with
**Always allowed** access, including access to Admin. A policy scoped to specific
agents cannot create new identities or attach arbitrary Codex sessions. For
example, send `/create Research partner`, then choose a button showing the root
session's name and working folder. No session ID needs to be copied. The new
identity appears immediately in the dashboard; connect its Telegram bot through
the Admin menu or the desktop app.
If Codex is unavailable, the identity remains unassigned and `/bind Research
partner` can be used later.

The picker lists available loaded roots on the default local Codex daemon,
with ten choices per page. Assigned sleeves, subagents, and unknown origins are
excluded. Custom sockets remain available through the desktop app. Each picker
belongs to its sender and admin bot, expires after five minutes or an app restart,
and is consumed on an assignment attempt. Access, root origin, current ownership,
and the identity's binding are checked again when assigning. Active sleeves must
be stopped or finished before replacement. Admin cannot replace its own sleeve
from its chat; use the desktop app for that change.

Send `/menu` or `/start`, select an agent, then choose **Set / change Telegram
token**, **Assign / Change sleeve**, or **Stop work**. The agent card shows cached
status, its current sleeve, and its Telegram username. **New agent identity** asks
for a name and opens the new identity's action menu. Scoped users see only their
accessible agents; configuration actions require Always allowed access.

Token and name inputs use Telegram's reply interface: reply to the specific
prompt within five minutes. `/cancel` or a new menu invalidates the previous
prompt. The server verifies tokens with Telegram before updating the connection,
rejects bots registered to another identity, and rechecks access and the expected
connection before saving. Tokens are not echoed or logged; incoming admin token
receipts are redacted before SQLite persistence, while an input is held only in
memory until verification. Restarting the app expires unfinished inputs; reopen
the menu and resend explicitly. The verified token remains in the identity's
private SQLite record. Admin's own bot and sleeve must be changed in the desktop
app to preserve the management connection.

`/list` reads cached observations without waiting for Codex. Admin command and
Telegram delivery workers are separate from normal workers, so a blocked Codex
request or another bot's delivery does not hold up listing. Each Codex entry shows
when its runtime was checked; a cached status is not proof of current reachability.
The former admin `/health`, `/agents`, and `/status` commands are removed.
Cached observation timestamps reset on restart; missing observations are reported
as such.

`/kill` preserves the identity and sleeve and never terminates the shared Codex
process. Queue cancellation is durable before the runtime request. A submission
that finishes after cancellation is followed by an interrupt request for its
returned turn. Runtime operations have a five-second deadline; the whole command
has a six-second deadline including configuration-lock waits. Failures report
that stopping was not confirmed; uncertain instructions are never replayed.
Work already performed cannot be undone.

AdminAppSleeve runs in the Agent Home server. It remains useful when Codex is
unresponsive, but it cannot respond when Agent Home or Telegram is unavailable.
An independently hosted monitor is required to diagnose a stopped host/server.

## Routing and recovery

Telegram voice notes are downloaded only for authorized senders with a reachable
Codex sleeve. The bridge saves the original audio in the sleeve's workspace at
`.local/agent-home/voice/`, with private file permissions and a local `.gitignore`.
It sends the agent the absolute file path and asks it to transcribe using its
available local tools, begin its first reply with `You asked: <transcribed text>.`,
and then answer or act on the spoken request. If it cannot decode the recording,
the agent is asked to explain which tool is missing. No transcription API, API
key, or server-side speech model is required; decoding capability belongs to the
assigned agent. Typed messages are forwarded unchanged.

The cloud Telegram Bot API limits voice downloads to 20 MB. Invalid, oversized,
unreadable, or failed downloads produce a warning without submitting work. The
bridge rechecks access after downloading, persists the voice path and exact
sleeve address with the receipt, and preserves the same turn-recipient routing
for replies. Active turns receive the file as steering input. AdminAppSleeve
continues to accept typed commands only. Audio files remain in the workspace so
the agent can read them and recovery receipts can reference them; remove them
manually once they are no longer needed. A cached recording isn't automatically
submitted to a replacement sleeve, and uncertain instructions aren't replayed.

When assigning or reconnecting an empty root that Codex cannot resume before its
first message, Agent Home sends one short initialization message and waits for
completion. Its receipt survives restarts, its dummy reply stays out of Telegram,
and an uncertain initialization is never sent again automatically. Existing
conversations receive no initialization messages.

Telegram photos, videos, video notes, animations, and image/video documents are
also accepted. The bridge downloads them into the exact sleeve's private,
Git-ignored `.local/agent-home/incoming/` folder. Photos and supported still-image
documents become native Codex image inputs; videos and animations include the
local file path and a request to inspect it using available local tools. Captions
are preserved verbatim. Album entries arrive as separate inputs. Downloads are
limited to 20 MB by Telegram's cloud Bot API; failed, invalid, and oversized files
produce a warning without submitting work. Files and their exact sleeve address
are retained with the receipt, with the same access, cancellation, and uncertain
submission rules as voice notes. Files remain until manually removed.

- In Codex sleeves, every non-empty text message starts work or steers the active
  turn. Text is forwarded verbatim, including whitespace, bot mentions, and slash
  prefixes such as `/goal`, `/help`, `/status`, and `/stop`. The bridge has no
  slash-command parser for Codex sleeves and does not translate these messages
  into terminal menu actions or goal-management RPCs. Their interpretation
  belongs to Codex. Only AdminAppSleeve handles bridge slash commands.
- Successful submission sends no routine “Instruction sent” acknowledgment.
  A missing or unreachable sleeve produces an actionable warning without
  submitting or replaying the instruction.
- Inspect runtime state in the local dashboard or through AdminAppSleeve's
  `/list`. Use AdminAppSleeve's `/kill <agent name>` to request interruption.
- Completed agent text is relayed, with commentary prefixed “Working:”. Tool
  output and reasoning are not mirrored. Command replies go to their sender.
- Markdown media embeds `![filename](filepath)` and links `[filename](filepath)` in
  agent replies upload the local image or video alongside the original text,
  including embeds within a paragraph
  and multiple embeds per line. For example, `![Video](outputs/final.mp4)` uploads
  that video. Angle-bracket paths and percent-encoded paths support spaces:
  `![Video](<outputs/final cut.mp4>)` or `[Download](<outputs/final cut.mp4>)`.
  Explicit `[[attach:filepath]]` markers are also supported, including one per
  line for multiple attachments.
  Bare paths, nonmedia links, HTTP URLs (including localhost playback links),
  and code examples stay text. Telegram work/steering messages are forwarded without
  appended prompts or formatting instructions. Relative paths resolve against
  the originating sleeve's working folder; absolute paths and local `file://`
  links must also stay inside that folder. Repeated references in one reply
  produce one attachment per recipient.
- JPEG, PNG, and WebP use photo messages; GIF uses animation; MP4/M4V use video.
  MOV, WebM, MKV, and AVI arrive as file attachments. If Telegram rejects inline
  display because of dimensions or encoding, the relay tries a file attachment.
  Upload limits are 10 MB for photos and 50 MB for videos, animations, and files.
  There is no automatic conversion or compression.
- Media jobs persist in SQLite and retry temporary delivery failures. Keep source
  files in place until delivery; the relay reads their current contents when
  uploading. Missing, invalid, oversized, or permanently rejected media sends
  a warning to the same Telegram recipient with its path and reason. Oversize
  warnings include the actual file size and upload limit. Uploads recheck current
  recipient access and validate the file type and workspace boundary. Symlinks and remote URLs are
  never uploaded automatically. Uploaded audio documents are not handled yet;
  Telegram voice notes follow the file-based flow above.
- Agent replies go to allowed participants in that turn. All participants share
  its conversation. A local-originated turn is mirrored automatically only when
  exactly one allowed private chat is connected.
- Command and file-change approvals use buttons scoped to identity, connection,
  exact thread, and turn. Changing sleeves invalidates old buttons. Unsupported
  input requests remain in the local Codex interface.

Incoming receipts and Telegram offsets commit together in SQLite before polling
advances. A crash during submission leaves an uncertain receipt that is never
automatically replayed. Check the local conversation before resending. Pending
replies survive restarts and retry Telegram failures. Outgoing delivery is at
least once: a crash after Telegram accepts a reply and before the checkpoint can
duplicate the reply, without intentionally replaying the instruction.

Restarting Agent Home with the same configured database restores assigned sleeves,
Telegram offsets, turn recipients, and pending deliveries. The bridge reconnects
to the exact Codex threads and recovers completed agent messages newer than its
saved turn checkpoint, including replies produced while Agent Home was stopped.
Codex work continues independently if its process stays running. Recovery waits
for an in-progress turn to finish; tool output and reasoning are not mirrored.
Delivery still requires the recipient's current communication policy to allow it.
If Codex is unavailable, the bridge keeps retrying the existing sleeve; it does
not create a replacement or restart the Codex process.

Use the stable Codex control socket, normally
`~/.codex/app-server-control/app-server-control.sock`, since its temporary resolved
destination can change after daemon restarts. Exact UUID and socket path identify
a sleeve; names, directories, nicknames, and PIDs do not. Model, folder, sandbox,
and approval policy remain under the existing Codex conversation’s control.

## SQLite migration and storage

On first use of an uninitialized database, `state.json` in its directory migrates
automatically. A private `state.before-sqlite.json` backup and the original JSON
remain intact. Earlier single-owner access rules also migrate. Once initialized,
the database is authoritative; restarts do not reimport the old JSON file.
Unsupported database or legacy state versions are refused.

SQLite versions 1–3 upgrade to schema 5 after a private
`<database-name>.before-external-identities.sqlite3` backup. Legacy tokens migrate
to direct tokens; token files remain untouched. Agent-level Telegram rules become
external identities with **Specific agents** policies. Repeated numeric accounts
merge their agent scopes, and known username pins remain bound to the same
account. No account is automatically granted access to all agents. Conflicting
username pins or malformed settings stop migration without changing the database.
The former agent policy column and per-bot sender pins are removed.

Version 4 upgrades create `<database-name>.before-admin-sleeve.sqlite3` privately
before rebuilding the sleeve table in a transaction. Schema 5 permits an admin
sleeve with no Codex address and uses a partial unique index to enforce the
singleton. Existing agent IDs, sleeves, external policies, credentials, and queues
remain intact.

SQLite tables hold agent identities, exclusive sleeves, external identities,
incoming receipts, and outgoing deliveries. A separate relation connects external
identities to allowed agent UUIDs, with foreign-key and uniqueness constraints. Small runtime collections (events, pending sender metadata, recipient
sets, and deduplication keys) are stored as JSON values in `runtime_state`. Each
checkpoint writes the working set in one SQLite transaction. This suits a small,
single-process local bridge; high message volumes would warrant incremental
writes. Database files are private (`600`); new parent directories use `700`.
Keep the database in a private directory. It contains conversation messages,
sender metadata, and bot tokens.

## Local API

Foreign Host/Origin headers, cross-site browser API requests, and non-JSON API
mutations are rejected. POST bodies must be JSON objects. Credentials are redacted.

| Method | Endpoint | Body / result |
|---|---|---|
| GET | `/api/state` | UUIDs, sleeves, runtime/listener status, external identities, unregistered senders, events |
| GET | `/api/settings` | Active database path, dashboard port, allowed launch folders, config path, editability |
| POST | `/api/settings` | `{"db_path":"/private/new/home.sqlite3","sleeve_allowed_folders":["/Users/me/projects"]}`; copies existing data if the path changes |
| POST | `/api/sleeves` | `{"folder":"/Users/me/projects/example","request_id":"<UUID>"}`; returns the created root's socket/thread and launch diagnostics |
| GET | `/api/threads?socket_path=…` | Loaded root-agent conversations for the chosen socket |
| POST | `/api/identities` | `{"name":"Research"}`; optional `telegram` |
| POST | `/api/identities/Research/sleeve` | `{"kind":"codex","thread_id":"<UUID>","socket_path":"/path/to/codex.sock"}` |
| POST | `/api/identities/Research/sleeve` | `{"thread_id":null}` detaches an idle or unreachable sleeve |
| POST | `/api/identities/Research/sleeve/recover` | `{"thread_id":"<current UUID>","socket_path":"<current socket>"}`; restores the currently assigned Agent Home-created sleeve |
| POST | `/api/identities/Admin/sleeve` | `{"kind":"admin_app"}` assigns the singleton; no other fields accepted |
| POST | `/api/identities/Research/communication` | `{"telegram":{"token":"<BotFather token>"}}` |
| GET | `/api/external-identities` | Registered external accounts and their policies |
| POST | `/api/external-identities` | Creates an external identity using the JSON representation above |
| POST | `/api/external-identities/<external UUID>` | `{"communication_policy":{"mode":"always_allowed","agent_ids":[]}}` |

In communication updates, omit `telegram` to retain the connection; send `null`
to disable it. Agent requests reject access-policy fields. The former agent
`/authorize`, `/allow-rules`, and `/communication-rules` endpoints are removed.
Unknown policies, malformed account settings, duplicate registrations, and scopes
referencing nonexistent agent UUIDs are rejected. URL-encode identity names. The
older `/binding` mutation endpoint and the state response's `binding` field remain
aliases for compatibility; use `sleeve` in new clients.

## Development

All code is developed on feature branches and reaches `master` through pull
requests. [AGENTS.md](AGENTS.md) defines module boundaries, configuration typing,
security, lifecycle invariants, and required verification. Pull requests to `master`
run the Python and frontend regression suites and verify the production build in
GitHub Actions.

The dashboard uses Vue 3 single-file components and Vite. Use Node 24 (the exact
version is recorded in `.nvmrc`) to develop the frontend:

```sh
nvm use
npm ci
npm test
npm run build
uv run python -m unittest -v
git diff --check
```

`npm run dev` starts the local Vite development server and proxies `/api` to the
Python service at `127.0.0.1:8765`. The production bundle in
`agent_home/static/build/` is committed so normal Python startup requires no Node
process, npm install, or external CDN. Rebuild and commit that directory with
frontend source changes; CI rejects stale or missing generated assets. The
backend serves only the built HTML and `/assets/` directory under its existing
content security policy, without runtime template compilation or `unsafe-eval`.

Frontend component tests cover creation, communication settings, pending senders,
external registration, policy modes, agent scope, failed saves, exclusive sleeve choices,
stale lookup cancellation, and keyed
updates that preserve action controls. Browser checks use disposable local data.

Tests use temporary SQLite databases and mocked providers, plus a real WebSocket
over a temporary Unix socket. They cover authorization, username pinning,
revocation, exact routing, steering, reply isolation, recovery, schema rollback,
legacy migration, configuration validation, sleeve replacement, communication
changes, and local API protections. They do not use live credentials or run model
turns.

- `agent_home/models.py`: agent, sleeve, Telegram, external identity, and communication-policy dataclasses.
- `agent_home/config.py`: typed service configuration.
- `agent_home/storage.py`: SQLite schema, checkpoints, and legacy migration.
- `agent_home/codex.py`, `telegram.py`: provider transports.
- `agent_home/service.py`: lifecycle, authorization, routing, and delivery.
- `agent_home/admin.py`: fixed administration commands and cached health reporting.
- `agent_home/web.py`: local API and dashboard serving.
- `frontend/src/`: Vue dashboard, table and dialog components, API client, styles, and component tests.
- `frontend/vite.config.js`: production build and local development configuration.
- `agent_home/static/build/`: generated Vue production bundle served by Python.
- `agent_home/cli.py`, `bridge.py`: startup and process locking.
- `test_bridge.py`, `test_admin.py`: automated regression checks, including singleton migration and independent admin dispatch/delivery.

This version supports Telegram text, outgoing and incoming images/videos, and incoming
voice-file forwarding. Additional external sleeve providers, automatic startup,
Slack, and WhatsApp are not implemented.


To run bounded live Telegram media checks with two dedicated test bots, enable
Bot-to-Bot Communication Mode for both in BotFather and save their tokens in a
private, Git-ignored JSON file (`{"FirstTestBot": "token", "SecondTestBot": "token"}`).
Run `uv run python -m scripts.test_telegram_media --credentials /private/tokens.json`.
The script creates two isolated test identities and root threads, tests text in
one direction, an image in the other, and a video back again, then stops. It keeps
its separate database, files, exact thread IDs, and redacted result report under
`.local/telegram-media-test/`. It never changes the installed app's database,
production bots, or communication policies. It requires a running local Codex
daemon and ffmpeg. A test reply is checked but never ingested as another task.


## Project website

The separate Vue site in `website/` introduces Agent Home with illustrative
Telegram conversations, feature details, installation instructions, and FAQs.
It never calls the local service API or includes a live workspace.

```sh
npm run site:dev
npm run site:test
npm run site:build
```

The site builds to `build/site/` with the `/agent-home-public/` GitHub Pages base path.
The Pages workflow builds on PRs and relevant master changes. Public deployment
uses a workflow dispatch on master with **publish** enabled, after the
privacy review. A successful tagged release triggers this dispatch automatically.
Only `build/site/` is uploaded, never the Python app,
local configuration, recordings, or database. Configure the repository's Pages
source as GitHub Actions when ready to publish.

## License

Copyright © 2026 firstprayer. Agent Home is licensed under
[GNU AGPL version 3 only](LICENSE) (`AGPL-3.0-only`). Distributed modified versions
must provide corresponding source under the same license. Modified versions
that users interact with over a network must offer those users their
corresponding source as required by section 13. Commercial use is permitted.
Third-party components retain their respective licenses; see
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Publishing a release

Make release changes on a feature branch and merge its PR after checks pass. The
release tag must match the version in `pyproject.toml`, and notes must exist at
`docs/releases/<tag>.md`. Push the tag from the reviewed `master` commit:

```sh
git fetch origin
git tag v0.3.2 origin/master
git push origin v0.3.2
```

The tag runs the regression checks and builds the DMG on GitHub's macOS runner.
Only after those checks pass does it publish a preview Release with the DMG and
SHA-256 checksums, then dispatch the Pages deployment. Publishing needs no
personal access token. Enable Pages with GitHub Actions as its source once in
repository settings. Never upload workstation-built packages without a separate
privacy review. Check the published downloads and website after deployment.
