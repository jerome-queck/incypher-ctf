# Credentials

The active operator file is the gitignored `.env`. It names exactly one Board. No `.env.incypher`
or `.env.<event>` overlay is supported.

## Setting up

Run `bash scripts/setup-board.sh`, or copy `.env.example` to `.env` and fill it with an editor.
The wizard stages changes privately and atomically replaces `.env`. It refuses before prompting if
another `.env.*` file exists. Never put a real secret in shell history, chat, or a commit.

Ask the machine what files and declared values it holds without printing values:

```bash
python3 scripts/credentials_held.py
```

## Active credentials

| Credential | Opens | Source | Leak response |
|---|---|---|---|
| `CTFD_API_TOKEN` | One CTFd account: challenge reads and submissions | Board **Settings → Access Tokens → Generate** | Delete it on that Board and generate another. |
| `TEAM_KEY` | IN-CYPHER team identity and raw-TCP proof-of-work gate | IN-CYPHER **Settings → Access Tokens**; displayed, not generated | It cannot currently be rotated from our side. Treat any disclosure as permanent and contact organisers when a supported channel exists. |
| Native Codex store | Native Codex inference, catalogue, limit state, and authorised resets | Run `codex login --device-auth` inside the container with `CODEX_HOME` on its private persistent store | Revoke the session and repeat login. |
| CPA OAuth store | Private CPA Responses Harness under the same Owner and allowance | Authenticate the pinned CPA service into its separate broker-private persistent store | Revoke the session, replace the store, and check the shared account state. |
| `CPA_TOKEN` | Local client capability for the private CPA service; not an inference API key | Minted by CPA setup and injected through `.env` to the Supervisor, which transfers it to the CPA broker | Replace the local client token and recreate any container that inherited it. |

Inference is closed by [ADR-0040](adr/0040-one-owner-one-subscription-and-one-observed-limit-state.md)
to native Codex and the independently supervised private CPA Harness. Both use Jerome's single
ChatGPT subscription and shared quota. No inference API key or alternate-provider credential belongs
in `.env`, the image, or an Attempt. CPA is a route-local fallback, not extra quota.

`CTFD_URL`, `RUN_ID`, `RUN_SECONDS`, and `CODEX_MODEL` are non-secret Run configuration. `RUN_ID`
must be stable across a restart. `RUN_SECONDS` may shorten but never extend the Board window.

## One environment authority

`.env` contains the official IN-CYPHER Board URL, CTFd token, Team key, Run identity, and optional
Run choices. Switching Boards means one atomic replacement, never composing files. Practice
credentials must not be mixed into the official file.

The Supervisor reads the environment once at Boot. It transfers Board material to the Board broker,
native auth authority to Codex Control, and CPA auth authority to the CPA service. Hostile executors
receive opaque, identity-bound capabilities and only `PATH`, `HOME`, `TERM`, and `LANG`; they receive
no raw credential or credential store.

Native Codex and CPA use distinct persistent stores outside shared Run state. The native login must
be taken before a Run:

```bash
docker run --env-file .env -v "$PWD/state:/state" -e CODEX_HOME=/state/codex solver:latest
codex login --device-auth
```

The production isolation profile further moves broker credential stores away from hostile mounted
state. Colima mounts `$HOME` only, so the repository and any host bind source must remain beneath it.

## Scanner vault

Evidence publication reads exact current and historical secret values from the macOS login
Keychain item with service `incypher-ctf.evidence-scanner-vault` and account `solver`. `.env` is
only live authority and never the history source. Publication refuses while any legacy overlay
exists.

The item is a JSON document with `schema_version`, `kind`, monotonic `version`, fresh random
`attestation_id`, `completeness_through`, and `values`. Every scanner name must exist. Active names
may use `current`; credentials removed from the live surface remain scanner-only vocabulary with an
empty `current` list so old evidence can still be checked. Rotated values move to `historical`.

Exact values—not hashes—are required because the scanner must find raw, encoded, malformed, and
structured occurrences. The vault never enters the image or published capsule. Promotion records
only its opaque attestation and completeness boundary.

## How values reach the container

Board values are injected at runtime:

```bash
docker run --env-file .env solver:latest
```

Never use `ARG`, `ENV`, or `COPY` for a secret. Image layers retain deleted material. Docker also
stores injected values in container configuration, so `docker inspect` output is sensitive and a
container created with stale values must be recreated—not merely restarted.

The local Gate creates a short-lived same-name env file with disposable fixture values, starts the
container, then deletes the file. It never copies official Board credentials.

## If a credential is committed

Stop. Do not merely delete it in a later commit: history still contains it. Rotate every rotatable
credential first, preserve its old value in the private scanner vault as historical, then clean the
branch and rerun conformance. For the non-rotatable Team key, stop distribution immediately and
escalate to the organisers before continuing.
