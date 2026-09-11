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
      # The sandbox `codex` *would* run challenge-supplied code in, and cannot here: bubblewrap
      # needs a mount namespace Docker will not grant an unprivileged container, and only
      # `--privileged` lifts enough of the confinement to let it — measured every way on 26 August
      # 2026, down to `bwrap: Failed to make / slave`. So v1 runs the CLI at `danger-full-access`
      # and the container is the only boundary, which is what ADR-0008 already says it is
      # (`solver/codex.py`, at `Invocation.sandbox`). The package stays because the decision that
      # put it here is unchanged and v2's uid separation is what brings it back into use.
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
# **Two binaries, not one.** From 0.147.0 the CLI routes its shell tool through a separate *code
# mode host*, and without it every command the model tries fails before it runs — "Code mode will
# fail closed" is the CLI's own wording and it means the tool refuses. Measured on a live Run against
# BrunnerCTF on 26 August 2026: `codex` alone installed cleanly, answered `--version`, reached the
# model, and then could not execute one shell command all Attempt. Half a CLI looks exactly like a
# whole one until something asks it to work.
ARG CODEX_VERSION=0.147.0
ARG CODEX_SHA256_ARM64=eb677c80f666b1ab8b4b1d083b66e8d614b1281d960bb6f9fd8ca98f58b38b90
ARG CODEX_SHA256_AMD64=0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36
ARG CODE_MODE_HOST_SHA256_ARM64=dfd4ff98ea4db30ed078af9c31b6f86e3da4836d0573aa87e225e5a5b54d3c7c
ARG CODE_MODE_HOST_SHA256_AMD64=0146adfaac8363ec9fcdb5895f7624db5b2e8617a283887938b7fb97a1dd4356
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      arm64) triple=aarch64-unknown-linux-musl; codex="$CODEX_SHA256_ARM64"; host="$CODE_MODE_HOST_SHA256_ARM64" ;; \
      amd64) triple=x86_64-unknown-linux-musl;  codex="$CODEX_SHA256_AMD64"; host="$CODE_MODE_HOST_SHA256_AMD64" ;; \
      *) echo "no codex build is mapped for TARGETARCH='$TARGETARCH'" >&2; exit 1 ;; \
    esac; \
    for part in "codex:$codex" "codex-code-mode-host:$host"; do \
      name="${part%%:*}"; digest="${part#*:}"; \
      curl -fsSL --retry 5 -o /tmp/part.tar.gz \
        "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/${name}-${triple}.tar.gz"; \
      printf '%s  %s\n' "$digest" /tmp/part.tar.gz | sha256sum -c -; \
      tar -xzf /tmp/part.tar.gz -C /tmp; \
      mv "/tmp/${name}-${triple}" "/usr/local/bin/${name}"; \
      chmod 0755 "/usr/local/bin/${name}"; \
      rm /tmp/part.tar.gz; \
    done

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
# The code mode host is asserted to **answer**, not merely to exist, for the reason the rest of this
# list is exercised on real input. What no build can prove is the thing that actually failed — that
# the model can run a command through it, which needs a login the image must never contain. That is
# `scripts/codex_probe.py`, run by hand before a Run.
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
    codex-code-mode-host --version >/dev/null 2>&1 || codex-code-mode-host --help >/dev/null 2>&1; \
    rm "$probe"

# The second list, and it has a different provenance from the one above: those tools were designed
# in from ADR-0005's cascade, these were **measured** — every one is a binary or a module the model
# itself reached for and did not find, across the 121 `shell` Steps of the `brunner-gate-2` and
# `brunner-gate-3` Runs on 26 August 2026 (#99). Eighteen of those Steps ended `command not found`.
# ADR-0024 is the per-line argument and the list of what was declined.
#
# It sits **after** the probe above rather than beside the floor list, for one reason that is pure
# build mechanics: the `codex` layer downloads ~100 MB pinned by digest, and a package added above
# it invalidates that download on every edit to this list. Nothing here is needed before that point.
#
# `imagemagick-7.q16`, and **not `imagemagick`** — that name is a metapackage whose entire content
# is a dependency on this one, so naming it would put the ban in ADR-0008 one indirection away from
# being true. `identify` and `montage` are this package's, on `/usr/bin`, with no alternatives dance.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends -o Acquire::Retries=5 \
      # `fc-list`. Worth a line only because the fonts are already in the image and nothing could
      # name them: DejaVu ships in the base, and without this the model cannot find a font file to
      # render text with — which is what PIL's `ImageFont.truetype` wants and its default cannot do.
      fontconfig \
      # A leaked `.git` is a whole genre of Web Challenge, and the model reached for it 5 times —
      # more than any other missing binary. 6 packages on top of what is already here. This is the
      # one line that reverses a decision rather than filling a gap: ADR-0009 kept "the Solver never
      # commits" by leaving the binary out, and ADR-0024 moves that enforcement to the test that was
      # always the one about it (`tests/test_two_homes.py`).
      git \
      # **`imagemagick-7.q16` is broken without this, and the probe below is how we found out.**
      # With DejaVu alone ImageMagick knows 8 fonts and resolves *none* of them as its default, so
      # every invocation that draws text — `-annotate`, `-label`, and `montage`'s own default tiling
      # — dies at `unable to read font ''` and exits 1 while still writing a plausible file.
      # `gsfonts` is the URW base-35 set its built-in default names; with it the font list is 76 and
      # the same commands exit 0. 6 packages, measured in this image on 27 August 2026.
      gsfonts \
      # `identify` and `montage`, reached for 3 times between them. The Challenge shape is an image
      # that has to be measured or tiled before it can be read.
      imagemagick-7.q16 \
      # 3 packages, and the model asked once. The cheapness is the argument: `jq` is how anything
      # reads a Board's JSON or a Challenge's config without writing a parser first.
      jq \
      # `pdftotext`, and the clearest single loss in the two Runs: `gate-3 / 69-1` paid an
      # `apt-get update` for it mid-Attempt and went on to solve, while `gate-2 / 69-1` met
      # `pdftotext: command not found`, spent five `web_search` Steps and closed `cut:novelty`.
      poppler-utils \
      # `python`. One package that is a symlink, and the model typed the bare name once. The base
      # ships `python3` only, so `python foo.py` — which is most of the internet's example code —
      # fails on a container that has a perfectly good interpreter.
      python-is-python3 \
      # PIL, reached for twice and the most-wanted module of the seven. Image work is Forensics and
      # Stego's floor, and `zsteg` above only answers PNG and BMP LSB.
      python3-pil \
      # `pip` itself. ADR-0024 argues the line; the marker removed below is what makes it true.
      python3-pip \
      # 1 package on top of what is here. The model reached for it once, and a PDF that `pdftotext`
      # renders as nothing is often one whose text is in an object `pypdf` will hand over.
      python3-pypdf \
      # 6 packages, and the module every piece of example HTTP code on the internet imports. The
      # stdlib `urllib` does the same job, which is again a Step spent finding out.
      python3-requests \
      # One package, no dependencies, and 4 reaches. `grep` is present and does the same job, but a
      # Step spent discovering that is a Step, and this is the cheapest line in the list.
      ripgrep \
 && rm -rf /var/lib/apt/lists/*

# **`pip` without this line is `pip` that does not install anything.** Debian marks its system
# Python externally-managed (PEP 668), so `pip install pillow` stops at
# `error: externally-managed-environment` *before* it opens a socket — which is the same Step lost
# that shipping `pip` was meant to buy back, just with a different message on it. Measured in this
# image on 27 August 2026, both ways.
#
# The marker exists to stop `pip` from breaking a system someone else depends on. Nobody depends on
# this one: it is rebuilt from this file and thrown away after a Run, and the alternative it asks
# for — a virtualenv per install — is two more Steps in the place we are trying to remove one.
# Glob rather than a version, because the base ships 3.13 and 3.14 side by side and which one
# `python3` points at is upstream's decision, not ours.
RUN rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED

# The second list exercised the way the first one is — on real input, never `command -v`, because a
# binary that is present and cannot run is the failure this layer exists to catch. This is not
# theoretical: the first draft went red on `montage`, which is how `gsfonts` came to be on the list.
#
# Every line here asserts *behaviour*, which for a library means output and never an import — an
# `import requests` that succeeds proves the package unpacked and nothing about whether it works.
#
# The two PDFs are built by different routes on purpose, because the two readers disagree about how
# broken a PDF may be. `pdftotext` reconstructs a missing xref, so a nine-line `printf` is enough
# for it; `pypdf` raises `PdfStreamError` on the same bytes and wants a real one, so PIL writes that
# one — which exercises a third thing for free, PIL's own PDF encoder. Neither needs the network.
#
# `pip` is asserted to get **past** PEP 668 and reach resolution, and the assertion is on the
# sentence it prints when it does rather than on the absence of the one it prints when it cannot —
# an absence also passes for a `pip` that died for some other reason. `--no-index` keeps it offline,
# because a build that reaches PyPI goes red when PyPI is slow, which is the redirector problem at
# the top of this file wearing a different hat. `pip3` is the name the Run actually reached for.
RUN set -eu; \
    work=$(mktemp -d); cd "$work"; \
    printf 'flag{probe}' > plain.txt; \
    printf '%s\n' '%PDF-1.4' \
      '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj' \
      '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj' \
      '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj' \
      '4 0 obj<</Length 43>>stream' \
      'BT /F1 24 Tf 20 100 Td (flag{probe}) Tj ET' \
      'endstream endobj' \
      '5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj' \
      'trailer<</Root 1 0 R/Size 6>>' > text.pdf; \
    test "$(rg -o 'flag\{probe\}' plain.txt)" = 'flag{probe}'; \
    test "$(printf '{"f":"flag{probe}"}' | jq -r .f)" = 'flag{probe}'; \
    test "$(python -c 'print("flag{probe}")')" = 'flag{probe}'; \
    git init -q .; \
    git -c user.email=probe@localhost -c user.name=probe add plain.txt; \
    git -c user.email=probe@localhost -c user.name=probe commit -qm probe; \
    test "$(git show HEAD:plain.txt)" = 'flag{probe}'; \
    fc-list | grep -q DejaVu; \
    python3 -c "from PIL import Image, ImageDraw, ImageFont; \
i = Image.new('RGB', (240, 60), 'white'); \
ImageDraw.Draw(i).text((10, 15), 'flag{probe}', fill='black', \
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)); \
i.save('one.png'); i.save('drawn.pdf')"; \
    test "$(identify -format '%wx%h' one.png)" = '240x60'; \
    montage one.png one.png -tile 2x1 -geometry +0+0 two.png; \
    test "$(identify -format '%wx%h' two.png)" = '480x60'; \
    # No `-font`: this is `gsfonts` on trial and nothing else. Without it the default font resolves
    # to nothing, this exits 1, and the tiling above would still have passed.
    magick -size 200x50 xc:white -annotate +10+30 'flag{probe}' drawn.png; \
    test "$(identify -format '%wx%h' drawn.png)" = '200x50'; \
    pdftotext -layout text.pdf - | grep -qF 'flag{probe}'; \
    python3 -c "import pypdf; page = pypdf.PdfReader('drawn.pdf').pages[0]; \
assert (int(page.mediabox.width), int(page.mediabox.height)) == (240, 60), page.mediabox"; \
    python3 -c "import requests; \
assert requests.Request('GET', 'http://probe/x', params={'f': 'flag{probe}'}).prepare().url.endswith('f=flag%7Bprobe%7D')"; \
    pip3 install --no-index --dry-run pypdf 2>&1 | grep -q 'Requirement already satisfied'; \
    test "$(pip --version | cut -d' ' -f2)" = "$(pip3 --version | cut -d' ' -f2)"; \
    cd /; rm -rf "$work"

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
# Locked v2 package fragments are installed before any generated rootfs or receipt enters the
# image, so an unavailable exact version fails the build without publishing assembled Tool bytes.
COPY tool-supply/generated/apt-packages.txt /tmp/tool-supply-apt-packages.txt
RUN set -eu; \
    if [ -s /tmp/tool-supply-apt-packages.txt ]; then \
      apt-get update; \
      xargs apt-get install --yes --no-install-recommends -o Acquire::Retries=5 \
        < /tmp/tool-supply-apt-packages.txt; \
      rm -rf /var/lib/apt/lists/*; \
    fi; \
    rm /tmp/tool-supply-apt-packages.txt
COPY tool-supply/generated/rootfs/ /
COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json /opt/solver/tool-supply/
COPY scripts/apply_tool_supply_modes.py /tmp/apply-tool-supply-modes.py
RUN python3 /tmp/apply-tool-supply-modes.py \
      --inventory /opt/solver/tool-supply/inventory.json --root / \
    && rm /tmp/apply-tool-supply-modes.py
COPY solver/ solver/
COPY docs/competitions/*.board.json boards/

# PID 1 is the Supervisor in exec form, with no shell between it and Docker. It owns one
# Run-controller Boot and forwards TERM/INT to that Boot's process group before bounded teardown.
ENTRYPOINT ["python3", "-m", "solver.supervisor"]
