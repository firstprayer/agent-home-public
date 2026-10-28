# User guide

[← README](../README.md) · [API and storage](api.md) · [Development](development.md)

Agent identities have stable UUIDs; their runtime sleeves can be assigned,
replaced, or restored. Only confirmed Codex root sessions are supported, and at
most one identity may hold the built-in AdminAppSleeve. Sender access is managed
across all identities through External Identities. Application storage and
allowed sleeve folders live under Settings.

The host computer must stay awake, online, and running Agent Home. Telegram and
your configured Codex provider handle their respective communications.

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

For explicit service configuration, copy [service.example.toml](../service.example.toml)
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
with validation at the load boundary; see [AGENTS.md](../AGENTS.md).

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

To join an assigned session from your computer, expand **Connect in terminal**
in the agent table or **Change sleeve** dialog, then click **Copy resume command**.
Paste the complete command into your terminal. It uses the current sleeve's full
UUID, socket, and working folder, quotes paths for the shell, and preserves the
session's existing permissions. If clipboard access is unavailable, select and
copy the displayed command manually. This does not create or reassign a sleeve.

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

**Create new sleeve** opens a paginated list of the application-level
`sleeve_allowed_folders` from desktop **Settings**. Select a folder, review its
full path, then confirm **Create and assign**. Admin starts an independent Codex
root with `--yolo` in that exact existing folder and assigns it to the selected
identity. No arbitrary folder input or shell command is accepted in chat. An
empty list disables creation; configure folders on the desktop first. The
picker expires after five minutes or restart, and changes to the folder list,
target binding, or management access invalidate it. Creation and assignment
recheck access after Codex requests. Confirmations are consumed once, and
uncertain launches are never automatically retried. If creation succeeds but
assignment fails, the sleeve remains available for explicit assignment and its
full UUID is reported; its independent process is not stopped. A confirmation
can take up to 90 seconds including initialization and assignment.

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
  links may point to other local folders by default. In the agent's **Settings**,
  turn off **Allow attachments outside the working folder** to restrict that
  identity to its originating sleeve's workspace. New and migrated identities
  default to allowing external media. The setting survives sleeve and Telegram
  changes and is checked again before uploading queued attachments. This does
  not change Codex permissions or where incoming files are saved. Repeated references in one reply
  produce one attachment per recipient.
- JPEG, PNG, and WebP use photo messages; GIF uses animation; MP4/M4V use video.
  Supported file contents take precedence over a mismatched extension: a JPEG
  named `.png` uploads as JPEG, with a `.jpg` filename. The original file is not
  renamed or converted. Unrecognized contents are still rejected. When container
  headers fit multiple formats, a compatible original extension is retained.
  MOV, WebM, MKV, and AVI arrive as file attachments. If Telegram rejects inline
  display because of dimensions or encoding, the relay tries a file attachment.
  Upload limits are 10 MB for photos and 50 MB for videos, animations, and files.
  There is no automatic conversion or compression.
- Media jobs persist in SQLite and retry temporary delivery failures. Keep source
  files in place until delivery; the relay reads their current contents when
  uploading. Missing, invalid, oversized, or permanently rejected media sends
  a warning to the same Telegram recipient with its path and reason. Oversize
  warnings include the actual file size and upload limit. Uploads recheck current
  recipient access, the current agent's attachment setting, and the file type.
  Workspace restrictions use the folder captured when the reply was queued.
  Symlinks and remote URLs are
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
