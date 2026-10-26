# API and storage reference

[← README](../README.md) · [User guide](guide.md) · [Development](development.md)

## SQLite migration and storage

On first use of an uninitialized database, `state.json` in its directory migrates
automatically. A private `state.before-sqlite.json` backup and the original JSON
remain intact. Earlier single-owner access rules also migrate. Once initialized,
the database is authoritative; restarts do not reimport the old JSON file.
Unsupported database or legacy state versions are refused.

SQLite versions 1–3 upgrade to schema 6 after a private
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

Version 5 upgrades create `<database-name>.before-external-media.sqlite3`
privately before adding the identity-level `allow_external_media` column in a
transaction. Schema 6 defaults this boolean to true for existing and new
identities. Agent UUIDs, bot credentials, access policies, sleeve addresses,
queued work, attachment jobs, and launch receipts are preserved. Older app
versions refuse schema 6; retain the private backup when testing an upgrade.

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
| GET | `/api/state` | UUIDs, sleeves, `allow_external_media` per identity, runtime/listener status, external identities, unregistered senders, events |
| GET | `/api/settings` | Active database path, dashboard port, allowed launch folders, config path, editability |
| POST | `/api/settings` | `{"db_path":"/private/new/home.sqlite3","sleeve_allowed_folders":["/Users/me/projects"]}`; copies existing data if the path changes |
| POST | `/api/sleeves` | `{"folder":"/Users/me/projects/example","request_id":"<UUID>"}`; returns the created root's socket/thread and launch diagnostics |
| GET | `/api/threads?socket_path=…` | Loaded root-agent conversations for the chosen socket |
| POST | `/api/identities` | `{"name":"Research"}`; optional `telegram` and boolean `allow_external_media` (default true) |
| POST | `/api/identities/Research/sleeve` | `{"kind":"codex","thread_id":"<UUID>","socket_path":"/path/to/codex.sock"}` |
| POST | `/api/identities/Research/sleeve` | `{"thread_id":null}` detaches an idle or unreachable sleeve |
| POST | `/api/identities/Research/sleeve/recover` | `{"thread_id":"<current UUID>","socket_path":"<current socket>"}`; restores the currently assigned Agent Home-created sleeve |
| POST | `/api/identities/Admin/sleeve` | `{"kind":"admin_app"}` assigns the singleton; no other fields accepted |
| POST | `/api/identities/Research/communication` | `{"telegram":{"token":"<BotFather token>"}}` |
| GET | `/api/external-identities` | Registered external accounts and their policies |
| POST | `/api/external-identities` | Creates an external identity; see the [account and policy fields](guide.md#communication-and-access) |
| POST | `/api/external-identities/<external UUID>` | `{"communication_policy":{"mode":"always_allowed","agent_ids":[]}}` |

In communication updates, omit `telegram` to retain the connection; send `null`
to disable it. Include `allow_external_media: false` to limit outgoing attachments
to the originating sleeve's working folder, or true to permit other local
folders. Omit it to preserve the current setting. Non-boolean values are rejected
before any change. This setting belongs to the identity and survives sleeve or
bot changes. It does not grant sender access or change Codex filesystem permissions.
Queued attachment jobs use the current setting at delivery; an upload already
in flight cannot be recalled. Agent requests reject access-policy fields. The former agent
`/authorize`, `/allow-rules`, and `/communication-rules` endpoints are removed.
Unknown policies, malformed account settings, duplicate registrations, and scopes
referencing nonexistent agent UUIDs are rejected. URL-encode identity names. The
older `/binding` mutation endpoint and the state response's `binding` field remain
aliases for compatibility; use `sleeve` in new clients.
