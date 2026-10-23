# Development and publishing

[← README](../README.md) · [User guide](guide.md) · [API and storage](api.md)

## Development

All code is developed on feature branches and reaches `master` through pull
requests. [AGENTS.md](../AGENTS.md) defines module boundaries, configuration typing,
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

## macOS packaging

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
The Pages workflow builds on PRs and deploys reviewed website changes merged into
master. A workflow dispatch on master with **publish** enabled can also publish
the site; successful tagged releases trigger this dispatch automatically.
Only `build/site/` is uploaded, never the Python app,
local configuration, recordings, or database. Configure the repository's Pages
source as GitHub Actions when ready to publish.

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
