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

# The single writable path (ADR-0008), host-mounted at run time. Deliberately not a `VOLUME`: that
# hands a container started without `-v` an anonymous volume that dies with it, which is exactly
# the missing mount we would want to notice.
RUN mkdir /state

# The five tools #65 names as the recon floor, each **exercised on real input** rather than looked
# up on `PATH` — `command -v` passes for a binary that cannot run, and `file -b --mime-type` is
# the exact call ADR-0005's cascade dispatches on. This is the floor and not the whole list: a
# `binwalk3` or `exiftool` that installs but misbehaves is not caught here, only one that fails to
# install at all.
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
    rm "$probe"

# Every gate Run is from a built image with **no source mount** (ADR-0008), so this is what a gate
# proves. ADR-0008 bakes in three things and all three are here: the tools above, `solver/`, and
# every event's `.board.json` — every event's rather than one, because which Board a Run plays is
# `CTFD_URL`'s decision at run time and an image carrying one event's rules would be an image per
# event. `solver/profile.py` selects between them by URL, so an image built for Brunner and pointed
# at the Danish board matches nothing and refuses rather than playing a strict no-AI board.
#
# No secret is an `ARG`, an `ENV` or a `COPY` anywhere in this file, and none ever will be:
# credentials arrive by `--env-file` at run time (`docs/credentials.md`), which is what lets the
# image be handed over without handing over the team key.
WORKDIR /opt/solver
COPY solver/ solver/
COPY docs/competitions/*.board.json boards/

# PID 1 is the Solver process — no supervisor, and exec form so no shell sits in front of it.
# v1's gate has to tell a clean exit from a restart loop, and anything between them blurs exactly
# that. It runs as root: the container boundary is the isolation a non-root user would buy, and
# CTF tooling that needs a privilege at 14:00 has nobody to ask for one.
ENTRYPOINT ["python3", "-m", "solver"]
