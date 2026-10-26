# Agent Home engineering guidelines

Apply these rules to code, tests, configuration, and documentation in this repository.

## Branch and pull request workflow

- Develop every code change on a feature branch. Create or switch to that branch before editing; never develop directly on `master`.
- Bring changes into `master` only by merging a pull request. Never commit or push changes directly to `master`, and never bypass the PR with a local branch merge.
- Keep each PR focused, describe its resulting behavior and migration implications, and include the relevant validation results. Complete checks before requesting review or merging.
- Maintainer-agent commits use `firstprayer <2390732+firstprayer@users.noreply.github.com>` as author and committer. Check the effective Git identity before committing; never include the maintainer's private name or email. Other contributors retain their own attribution.

## Local deployment preference

- Prefer the installed DMG application at `/Applications/Agent Home.app` for normal operation. Keep the real service on the packaged runtime; do not replace it with a source-launched `bridge.py` process during development or verification.
- Build and test a new DMG before upgrading, quit the installed app gracefully, replace its bundle, and reopen it with the existing service configuration and database. Verify the native window, agent identities, communication policies, Telegram listeners, and assigned sleeves after the switch. Never stop Codex to upgrade Agent Home.
- Use source servers only for development with a disposable database and a separate port. Do not add login startup or automatic updates without a request.

## Domain and lifecycle

- `AgentIdentity.id` is an immutable UUID. Identity survives sleeve loss, reassignment, and communication changes. External access policies reference its UUID. Names are unique, immutable local labels.
- `AgentSleeve` is a replaceable runtime address. A Codex sleeve is identified by both socket path and exact thread UUID. Never route by title, folder, nickname, or PID.
- Only confirmed root agents may be Codex sleeves. Filter subagents and unknown origins from selection and reject them on assignment and reconnect. The UI selects a root agent from the list; it must not expose a manual conversation-ID input.
- Explicit sleeve creation starts an independent `codex resume --yolo` process for a newly created exact root thread in an allowed existing folder. Keep its PTY owner detached from Agent Home; never terminate or restart it as part of app shutdown, upgrades, reassignment, or policy changes. Use the exact socket/thread address for routing, never its PID. A root with no first user message is a valid empty sleeve; normalize only Codex's specific unmaterialized-history error to an empty page.
- `AdminAppSleeve` is the built-in fixed-command sleeve (`admin_app`), with no Codex address. At most one identity may hold it; enforce this with the configuration lock and a SQLite unique index. Never execute arbitrary text as shell commands or model instructions.
- An identity has at most one current sleeve; a sleeve belongs to at most one identity. Enforce both in the service and database.
- Never submit work without reaching the current sleeve. Do not silently create a sleeve or replay failed/uncertain instructions. Explicit replacement of an unreachable sleeve is allowed; a reachable active turn must be stopped or finished first.
- Communication belongs to the identity. Replacing a bot clears the previous bot's queued input, deliveries, pending senders, turn recipients, and approvals. Retain uncertain receipts for review.

## Module boundaries

- `models.py`: domain dataclasses and communication-policy parsing, without network or persistence.
- `config.py`: typed service configuration and boundary validation.
- `storage.py`: SQLite schema, transactions, and legacy migration. Other modules must never execute SQL.
- `codex.py` / `telegram.py`: provider transport and protocol handling. Do not put UI or identity assignment policy here.
- `media.py`: local media-reference parsing and file validation. Apply the identity's `allow_external_media` setting; do not fetch remote URLs or follow symlinks for automatic uploads.
- `incoming.py`: Telegram image/video selection, bounded private workspace storage, and media-specific Codex input construction. Still images use native `localImage` inputs; videos remain local files for the agent's tools. Preserve captions and never add media instructions to ordinary text.
- `voice.py`: private workspace-local voice-file storage and the voice-specific request to the agent. Decoding belongs to the assigned agent; do not introduce a transcription API or speech-model dependency in the bridge.
- `sleeves.py`: allowed-folder validation, Codex executable discovery, explicit daemon bootstrap, and the independent PTY keeper. Use argument arrays, never a shell command built from user input.
- `service.py`: authorization, message routing, lifecycle, and delivery orchestration.
- `admin.py`: fixed AdminAppSleeve commands, cached diagnostics, and bounded cancellation. `admin_menu.py`: sender-scoped menus and explicit configuration replies. Keep these paths independent of Codex work and delivery queues.
- `web.py`: local HTTP validation, serialization, and static assets; delegate lifecycle changes to the service.
- Frontend: separate framework components, dialog flows, and API access. Use accessible controls and the framework's escaped text bindings for untrusted content.
- Keep `bridge.py` as the entry point. Avoid provider interfaces, dependencies, options, or layers until a concrete second implementation requires them.
- `packaging/macos/`: native AppKit/WKWebView shell and reproducible DMG build. Reuse the Vue interface and the same locked Python service; keep databases, settings, tokens, recordings, and speech models outside the bundle. The desktop launcher owns only its bridge child, preserves single-writer locks, and must stop that child on quit or launcher loss without stopping Codex.

## Frontend development

- Always use a frontend framework for web development. Never implement application interfaces with vanilla JavaScript or manual DOM rendering.
- Use the selected framework's components, state management, and rendering conventions. Reuse the project's framework when one is established.
- This dashboard uses Vue 3 single-file components in `frontend/src/`, built with Vite. Keep the API client separate from components; use Vue bindings and keyed lists for application rendering.
- Existing vanilla JavaScript interfaces must migrate to a frontend framework when frontend development resumes; do not extend the vanilla implementation.
- Commit the production bundle in `agent_home/static/build/` with source changes so the Python service runs without Node at runtime. Never edit the generated bundle by hand; CI rebuilds it and rejects stale output.
- Keep source styles and Vue templates readable. Minification belongs to the production build.
- `website/` is a separate Vue project site. It must work without the local service; use clearly labeled fictional workspaces for demonstrations. Upload only its `build/site/` artifact to Pages. After privacy review, website changes deploy from reviewed `master` pushes; the explicit `publish` workflow input remains available and tagged releases dispatch it automatically. Publish only CI-built packages with checksums after the release checks pass.

## Configuration and storage

- Service configuration is loaded into the dedicated frozen `ServiceConfig` dataclass. `db_path` is part of that configuration. Resolve relative paths against the config file, reject unknown fields, and validate values at startup.
- Top-level Settings owns application-wide storage and `sleeve_allowed_folders`; do not put either setting on agent identities. Empty allowed folders deny creation. Resolve actual directories before boundary checks and reject sibling-prefix, traversal, and symlink escapes. This controls launch locations, not the filesystem privileges of `--yolo`.
- Database relocation must preserve the working set, queues, credentials, and launch receipts. Hold destination process locks before copying, refuse existing destination files, atomically commit the service config, then switch the live store without yielding. Retain the original private database and all acquired source locks until exit. Keep future identity-backup settings at application level.
- **Every YAML file consumed by Python must map to a dedicated `@dataclass`.** Convert and validate it once at the load boundary, including nested structures. Business logic must receive typed records rather than raw YAML dictionaries. Do not introduce YAML for a use already covered by TOML or existing formats.
- SQLite is the source of truth; `Store.data` is the single process's working set. Commit all related checkpoint changes in one transaction, including update offsets and incoming receipts. Never acknowledge a Telegram offset before its receipt is durable.
- Migrate legacy state only into an uninitialized database. Back up the source privately and preserve the original. Never overwrite an unsupported schema version or replay migration on every restart.
- Keep databases and backups private. `TelegramIdentity.token` holds the bot token directly in the local database; accept it through the communication form or an explicit Admin menu prompt, never return it in API responses or logs, and keep credentials and local config out of Git. `bot_id` is a read-only Telegram identifier obtained with `getMe`, not a credential.
- The service is a single writer. Hold the database/legacy-directory process locks for its full lifetime. Serialize configuration changes and per-identity routing to avoid assignment and communication races.

## Security, recovery, and verification

- Bind to loopback. Preserve Host, Origin, JSON content-type, and browser cross-site checks. Validate at network/configuration boundaries and deny access by default.
- Access belongs exclusively to the global `ExternalIdentity` registry, never to an agent or bot connection. Resolve the real provider sender first; reject unregistered senders. Apply exactly that identity's `communication_policy`: `always_allowed`, `always_blocked`, or `specific_agents` containing stable agent UUIDs. Empty scopes reject all. New registrations default to blocked; reject malformed policies, duplicate accounts, and unknown agent references.
- Pin Telegram external accounts to numeric sender IDs once known, across all bots. Match known numeric identities before unbound usernames, never fall back to a recycled username, and never auto-register a sender. Keep permissions through sleeve/bot changes. Migrate old access only into specific-agent policies; refuse conflicting historical pins rather than widening access.
- Recheck access for queued input, outgoing delivery, and approval callbacks. Scope approvals to identity, socket connection, exact thread, and turn. Clear them when a sleeve or bot changes.
- Admin commands require access to the admin identity and every target identity. `/kill` cancels queued instructions and interrupts the exact root turn; never kill a shared Codex process or delete identities. Persist cancellation before RPCs, recheck access before interrupts, and report unconfirmed stops honestly. `/list` must show each accessible agent's status and sleeve assignment using timestamped cached observations without waiting on Codex. AdminAppSleeve shares the server's availability and cannot diagnose a stopped server from inside that server.
- Admin creation, binding, and Telegram configuration require an `always_allowed` external identity. Reuse service mutations, rechecking permission after awaited RPCs and before persistence. Menu buttons and input prompts are scoped to the sender, admin bot, target UUID, and expected binding/connection; expire them on restart or after five minutes. Never steal another identity's sleeve. Keep new identities if discovery fails and report their unassigned state. Token input must reply to the delivered prompt; redact receipts before persistence, hold unverified input only in memory, never echo credentials, and preserve the old connection on failed/revoked verification. Admin cannot change its own connection or sleeve through its chat.
- Admin sleeve creation selects an exact folder from the configured `sleeve_allowed_folders` list and requires explicit confirmation. Recheck management access, the expected binding, and folder configuration before launch and assignment. Consume confirmations once, preserve uncertain launch receipts without replay, and report a created but unassigned sleeve without terminating its independent process. Desktop resume commands use the current exact address, shell-quote arguments, and preserve existing Codex permissions.
- Preserve Codex model, working folder, sandbox, and approval settings. Never log or return bot tokens; redact provider errors.
- Send no routine “instruction sent” acknowledgment. Warn if a sleeve is missing or unreachable. Preserve explicit command replies, requested approvals, agent replies, and actionable delivery/recovery errors.
- Forward Telegram work and steering text to Codex without appending prompts or formatting instructions.
- Codex sleeves receive all non-empty text verbatim, including slash-prefixed messages, whitespace, and bot mentions. Only AdminAppSleeve parses bridge slash commands. Do not intercept `/goal`, `/help`, `/status`, `/stop`, or unknown slash commands on Codex sleeves, and keep early reply routing enabled for those messages.
- Represent authorized Telegram voice notes with a private, Git-ignored file in the exact sleeve's workspace and a voice-specific instruction to decode it, begin the first reply with `You asked: <transcribed text>.`, and handle the spoken request. Recheck access and cancellation after downloads, bound downloads to 20 MB, retain the file path and exact sleeve address with the receipt, and warn on failures without submitting work. Never send recordings to a third-party transcription API. Admin commands remain typed-only, and uncertain voice instructions must not be replayed automatically.
- Apply the same private storage, 20 MB download bound, access/cancellation rechecks, exact-sleeve receipts, and no-replay rules to incoming photos and videos. Validate content before inference, reject unsafe download locations and redirects, keep original captions, and handle albums as separate messages. Register test-bot external identities only for their intended test agents; bounded live tests must stop after their expected acknowledgments instead of feeding replies into an automatic loop.
- Relay local images/videos from Markdown embeds `![filename](filepath)`, links `[filename](filepath)`, and explicit `[[attach:filepath]]` markers outside code spans/blocks in agent replies. `AgentIdentity.allow_external_media` defaults to true, including migrated identities, and allows local attachments outside the originating sleeve's workspace. When false, enforce the captured originating workspace. Keep relative paths bound to that workspace, recheck the current identity setting at delivery, and never persist upload permission on a job. Bare paths and nonmedia links stay text. Persist attachment jobs with the reply, recheck recipient access before uploading, validate regular files/content/size without following symlinks at delivery, and turn permanent attachment failures into a text explanation. Retry transient transport failures; never fetch arbitrary URLs or upload paths from incoming Telegram messages automatically.
- Keep transport readers free to receive RPC responses; never start an awaited RPC inside its own WebSocket reader callback.
- Explicit sleeve creation sends exactly one short initialization message, then confirms the exact thread can resume before starting its CLI. Persist the initialization turn ID and never replay uncertain initialization. The separate initialization of an assigned, confirmed unmaterialized root must also be persisted before submission, never replayed when uncertain, and hidden from Telegram replies. Never append initialization instructions to ordinary work or steering.
- Explicit recovery restores only the exact current Agent Home-created sleeve. Validate the expected binding, root, folder, and permissions; start only the default local daemon when needed. Reuse a live CLI and hold a per-sleeve lock for each new detached keeper. Never send initialization or replay user tasks during recovery. Keep daemon session status distinct from CLI process liveness, and retain all creation receipts when bindings or worker PIDs change.
- Every nontrivial behavior change needs a focused regression check. Cover migration/restart, access revocation, routing isolation, and failure before versus after submission. Test with temporary databases and mocked providers, never live credentials.
- Run `npm test`, `npm run build`, and `uv run python -m unittest -v`, and check the UI when changing layouts or dialogs. Update README examples and API contracts with behavior changes.
