# The Solver's image — one image for every Board, configured at run time (ADR-0008).
#
# Pinned by the base's **multi-arch index digest**, not a tag and not a per-architecture manifest
# digest, so this one line builds natively on the arm64 Mac we compete from and on the amd64
# runner that proves it. A tag would let the base move under us between the gate Run and the
# scored Run, which is the one window where nobody is watching.
FROM kalilinux/kali-rolling@sha256:ef7a551400b01dc501ff97f192c5b2b1ec629576dab5032822190cd2684ca4e1

# `http.kali.org` is a redirector that geo-picks a volunteer mirror, and the one it picks from
# Singapore answers 503 for whole pools — so the build fails for a reason that has nothing to do
# with the change being built, and a red check stops naming one change (`CODING_STANDARDS.md` §5).
# `kali.download` is Kali's own CDN and is the same host from everywhere. The `grep` is because a
# `sed` that matched nothing exits 0: without it, an upstream change to this file's wording would
# put the build back on the redirector silently.
RUN sed -i 's|http://http.kali.org/kali/|http://kali.download/kali/|' /etc/apt/sources.list.d/kali.sources \
 && grep -q 'kali.download' /etc/apt/sources.list.d/kali.sources

# ARG rather than ENV: the frontend is a fact about this build, and leaving it in the image would
# silently change how anything a Run installs later behaves.
ARG DEBIAN_FRONTEND=noninteractive

# The base is 79 packages with no `python3` and no `file`, so every tool ADR-0005's recon cascade
# dispatches on is named here. One package per line and no metapackage: `kali-tools-top10` answers
# the same need by pulling several hundred packages named nowhere a reviewer reads.
#
# `--no-install-recommends` is what keeps the list honest, and `ca-certificates` is the line that
# shows why it is worth the cost: it is a Recommends of `wget`, so without the flag it would
# arrive by accident and nobody would learn it was never listed. `foremost` and `gdb` arrive by
# neither route — nothing here pulls them and neither is named — so carving is `scalpel` and
# filesystem work is `sleuthkit`, by choice rather than by whatever came along.
#
# **`binwalk3`, and not `binwalk`.** Kali packages both and they do not conflict: `binwalk` is
# 2.4.3 at `/usr/bin/binwalk`, `binwalk3` is 3.1.0 at `/usr/bin/binwalk3`. Install both and a
# recon script that types `binwalk` silently gets the older one — which is the wrong one, because
# v2 **refuses to extract as root**: `binwalk -e` raises a ModuleException demanding `--run-as`,
# and this container runs as root by decision. v3 extracts as root cleanly. Only v3 is installed,
# so typing `binwalk` is `command not found` in a test rather than an exception at 14:00 on
# competition day.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends -o Acquire::Retries=5 \
      # `strings`, which the recon floor runs over every artefact including the unidentifiable.
      binutils \
      binwalk3 \
      # The sandbox `codex` runs challenge-supplied code in. Absent, it falls back to a copy it
      # bundles, and the thing under test stops being the thing we shipped.
      bubblewrap \
      bzip2 \
      # The one whose absence is invisible: without it `/etc/ssl/certs` does not exist, `curl`
      # installs perfectly, and every TLS handshake fails afterwards.
      ca-certificates \
      curl \
      file \
      # `exiftool`. There is no package by that name.
      libimage-exiftool-perl \
      openssl \
      python3 \
      # `zsteg`'s interpreter, for the gem installed below.
      ruby \
      scalpel \
      sleuthkit \
      unzip \
      wget \
      xxd \
      xz-utils \
      zstd \
 && rm -rf /var/lib/apt/lists/*

# **`zsteg` comes from RubyGems because Kali does not package it at all.** It is the standard first
# tool on PNG and BMP LSB stego, so the choice was its cost against dropping it: measured here,
# `ruby` is ~27 MB and 2 s and the gem needs no compiler and no `ruby-dev`, 3 s. That is cheap
# enough that dropping it buys nothing. The option actually ruled out is the third one — a
# `gem install` at run time makes a solve depend on RubyGems being reachable from the venue
# network at 14:00, with nobody present when it is not.
RUN gem install --no-document zsteg

# The vendor's agent itself — the thing `solver/codex.py` spawns. Half of this dependency was
# already here: `bubblewrap` above is installed *for* `codex`, and the reason is written beside it.
#
# **The standalone musl binary, not the npm package.** `npm install -g @openai/codex` would put a
# Node runtime and its dependency tree in the image in order to launch one statically linked binary
# that the same release already publishes. Nothing else here wants Node, and a runtime nobody else
# uses is a runtime nobody notices breaking.
#
# **A per-architecture download is a trap, so it is said out loud.** The run-day image is built on
# the arm64 Mac that runs it and CI proves amd64 (ADR-0008), so a URL naming one of them would build
# green on the runner and 404 on the machine that competes. `TARGETARCH` is BuildKit's own, set for
# the platform actually being built, and the `case` is exhaustive rather than defaulted — an
# architecture nobody mapped fails the build here rather than shipping an image with no agent in it.
#
# Pinned to a release **and to its bytes**. A GitHub release asset can be replaced under its own
# tag, and the window between the gate image and the run-day image is the one nobody is watching —
# the same argument the base image's digest pin makes at the top of this file.
ARG CODEX_VERSION=0.147.0
ARG CODEX_SHA256_ARM64=eb677c80f666b1ab8b4b1d083b66e8d614b1281d960bb6f9fd8ca98f58b38b90
ARG CODEX_SHA256_AMD64=0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) triple=aarch64-unknown-linux-musl; digest="$CODEX_SHA256_ARM64" ;; \
      amd64) triple=x86_64-unknown-linux-musl;  digest="$CODEX_SHA256_AMD64" ;; \
      *) echo "no codex build is mapped for TARGETARCH='$TARGETARCH'" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 -o /tmp/codex.tar.gz \
      "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${triple}.tar.gz"; \
    printf '%s  %s\n' "$digest" /tmp/codex.tar.gz | sha256sum -c -; \
    tar -xzf /tmp/codex.tar.gz -C /tmp; \
    mv "/tmp/codex-${triple}" /usr/local/bin/codex; \
    chmod 0755 /usr/local/bin/codex; \
    rm /tmp/codex.tar.gz

# The single writable path (ADR-0008), host-mounted at run time. Deliberately not a `VOLUME`: that
# hands a container started without `-v` an anonymous volume that dies with it, which is exactly
# the missing mount we would want to notice.
RUN mkdir /state

# The five tools #65 names as the recon floor plus the agent that drives them, each **exercised on
# real input** rather than looked up on `PATH` — `command -v` passes for a binary that cannot run,
# and `file -b --mime-type` is the exact call ADR-0005's cascade dispatches on. This is the floor
# and not the whole list: a `binwalk3` or `exiftool` that installs but misbehaves is not caught
# here, only one that fails to install at all.
#
# `codex --version` is asserted to carry the pinned version rather than merely to exit 0, because
# the two ways this line can be wrong are a binary that does not run and a binary that is not the
# one we pinned — and the second is invisible without it.
#
# It is a cached layer, so it re-runs when the lines above it change rather than on every `docker
# build` — which is what matters, because what it guards against is a package list that stopped
# meaning what it says.
RUN set -eu; \
    probe=$(mktemp); \
    printf 'flag{probe}' > "$probe"; \
    test "$(file -b --mime-type "$probe")" = 'text/plain'; \
    test "$(strings "$probe")" = 'flag{probe}'; \
    test "$(od -An -c "$probe" | tr -d ' \n')" = 'flag{probe}'; \
    test "$(python3 -c 'print("flag{probe}")')" = 'flag{probe}'; \
    if timeout 1 sleep 5; then echo 'timeout did not stop a command' >&2; exit 1; fi; \
    codex --version | grep -qF "$CODEX_VERSION"; \
    rm "$probe"

# Every gate Run is from a built image with **no source mount** (ADR-0008), so this is what a gate
# proves. ADR-0008 bakes in three things and this bakes two: the event's `.board.json` is the
# third, and it arrives with #74, which owns the profile it configures.
#
# No secret is an `ARG`, an `ENV` or a `COPY` anywhere in this file, and none ever will be:
# credentials arrive by `--env-file` at run time (`docs/credentials.md`), which is what lets the
# image be handed over without handing over the team key.
WORKDIR /opt/solver
COPY solver/ solver/

# PID 1 is the Solver process — no supervisor, and exec form so no shell sits in front of it.
# v1's gate has to tell a clean exit from a restart loop, and anything between them blurs exactly
# that. It runs as root: the container boundary is the isolation a non-root user would buy, and
# CTF tooling that needs a privilege at 14:00 has nobody to ask for one.
#
# `solver/__main__.py` arrives with #74, which owns boot refusal and clean termination.
ENTRYPOINT ["python3", "-m", "solver"]
