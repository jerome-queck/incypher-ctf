# The image carries what a Run reached for, and a picture is attached

> **The global run-time package-install path is superseded for v2 Gate and scored Runs by
> [ADR-0047](0047-one-immutable-image-exposes-only-proved-free-tool-components.md).**
> The measured resident floor and picture attachment stand; v2 requires every admitted component
> to be pinned, prebuilt and proved inside the immutable image instead of installed during a Run.
> This is a planning requirement, not a claim that the current image has passed it.

> **The claim that every Turn is a fresh Codex process is superseded by
> [ADR-0042](0042-one-controller-owns-four-agent-roles-and-every-engagement.md).** Tool-image and picture
> requirements stand; one persistent Solve Lead now spans an Attempt's Turns.

**Twelve packages join the `Dockerfile`, each one a binary or a module the model itself reached for
and did not find on a live Run — and `codex exec -i` now attaches a Challenge's pictures to the
turn, because the model reads them.** Both halves answer
[#99](https://github.com/jerome-queck/incypher-ctf/issues/99). The package half amends
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md)'s list without touching
its rules — still one package per line, still no metapackage, still nothing installed at run time.
The picture half is new behaviour at the seam ADR-0014 put in `solver/codex.py`.

This record also **moves one of ADR-0009's two enforcements**, and that is the only thing here a
reader should expect to argue with. It is in *`git`, and the rule it looks like it breaks* below.

## What the Runs measured

`brunner-gate-2` and `brunner-gate-3`, 26 August 2026, 121 model-driven `shell` Steps between them.
**Eighteen ended `command not found`**, over nine distinct binaries — `git` ×5, `rg` ×4, `pdftotext`
×2, `identify` ×2, `montage`, `jq`, `pip3`, `python`, `fc-list`. Seven imports failed the same way:
`PIL` ×2, `cv2`, `matplotlib`, `pypdf`, `requests`, `shapely`. And `pip` was absent too, so the
first workaround failed before the second could be tried:
`python3 -m pip install --quiet pillow` → `No module named pip`.

The eval queries over all four promoted gate Runs put the cost in the same frame: 20 of 27 Attempts
ended `cut:step-cliff`, consuming 83.8 of 92.6 minutes. The cliff counts model Steps, and a Step
that fails `command not found` and then fails the same way again mints no Checkpoint. Some share of
that cliff is the Solver spending its budget discovering what is missing from its own image.

## The rule applied per line

ADR-0008 asks for a list argued per package, so there is a test each line had to pass. **A package
is on the list when a Run reached for it and it is either cheap or its absence has already cost an
Attempt.** A package is off it when its cost would change the image's character, or when it is a
*domain* library rather than a general-purpose primitive — because a domain has no edge. `shapely`
came with `pyproj`; the next geospatial Challenge wants `geopandas` and `gdal`, and no list closes
that set. Run-time `apt-get` is what a domain library is for, and it is proven to work: `gate-3 /
66-1` installed exactly those two mid-Attempt, exit 0.

The per-line reasons live beside the lines, in the `Dockerfile`, where a reviewer reads them. The
marginal cost of each was measured on the built image on 27 August 2026 — `fontconfig`, `ripgrep`,
`python-is-python3` and `python3-pypdf` are one package each; `jq` and `python3-pip` three; `git`,
`gsfonts` and `python3-requests` six; `python3-pil` thirty; `imagemagick-7.q16` thirty-five;
`poppler-utils` forty-one. The whole layer is 219 MB and takes the image from 974 MB to 1.28 GB.
That is affordable because the image is built on the machine that competes and never pulled at
14:00 — the same fact that makes ADR-0008's "everything is baked in" worth having.

Two lines were not on anyone's list and are here because the build-time probe went red.

- **`gsfonts`** — `imagemagick-7.q16` alone knows 8 fonts and resolves **none** of them as its
  default, so `-annotate`, `-label` and `montage`'s own default tiling all die at
  `unable to read font ''`, exit 1, *while still writing a plausible output file*. With `gsfonts`
  the font list is 76 and the same commands exit 0. A tool that is present and quietly wrong is
  worse than one that is absent, and this is precisely the failure ADR-0008's "exercised on real
  input, never `command -v`" exists to catch. It caught it.
- **`fontconfig`** — cheap on its own, and the reason it is worth a line is that DejaVu already
  ships in the base and nothing could name it. `fc-list` is how PIL's `ImageFont.truetype` gets a
  path to pass.

## `pip` ships, and the marker is what makes that true

`pip` on its own would have bought nothing. Debian marks its system Python externally-managed
(PEP 668), so `pip install pillow` stops at `error: externally-managed-environment` **before it
opens a socket** — measured in this image both ways on 27 August 2026. Shipping `python3-pip` and
stopping there would have replaced one lost Step with a differently-worded lost Step.

So the marker goes: `rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED`. The marker exists to stop `pip`
from breaking a system other people depend on; nobody depends on this one, which is rebuilt from
the `Dockerfile` and thrown away after a Run. The alternative it recommends — a virtualenv per
install — is two more Steps in the place we are trying to remove one. The glob rather than a
version is deliberate: the base ships Python 3.13 and 3.14 side by side and which one `python3`
points at is upstream's decision.

What `pip` is **not** is a substitute for the list. It reaches PyPI, so it depends on the venue
network at 14:00 — exactly the dependency the `Dockerfile` already refuses to accept for a run-time
`gem install`. That is the argument for baking the eleven in and keeping `pip` as the long tail.

## `git`, and the rule it looks like it breaks

[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)'s *Two homes* says the Solver
never commits mid-Run, and it kept that two ways: no `git` binary in the image, and no `solver/`
module reaching for one. **This record keeps the rule and drops the first enforcement.**

The rule's subject is the Solver. A Challenge shipping a leaked `.git` directory — a whole genre of
Web Challenge, and the binary the model reached for more than any other — neither commits our record
nor writes outside `/state`. Meanwhile the mechanical guard was never the one doing the work:
[ADR-0018](0018-the-container-is-the-only-sandbox-v1-has.md) already concedes that the model runs as
root at `danger-full-access` with nothing between it and `/state`, so "no binary on `PATH`" stops an
accident and never a determined process. The guard that actually binds our own code is the AST test
over every `solver/` module, and it is untouched — `tests/test_two_homes.py` now asserts both halves:
that `git` is on the list on purpose, and that no module reaches for it.

Reversing this is one line in each file if the Owner disagrees.

## What is declined, so the next Run does not rediscover it

| Declined | Why |
|---|---|
| `python3-opencv` (`cv2`) | **325 marginal packages.** It would be the largest single thing in the image by a wide margin, for one reach. |
| `python3-matplotlib` | 77 marginal packages, and what it produces is a picture for a human to look at. Until a Run is measured spending a Checkpoint on a plot, this is cost without evidence. |
| `python3-shapely`, `python3-pyproj` | Cheap, and still out: a domain library, not a primitive. `gate-3 / 66-1` installed both mid-Attempt at exit 0, which is the run-time path working as designed. |
| `gdb`, `nc`, `socat`, `tshark`, `steghide`, `tesseract-ocr`, `ffmpeg`, a decompiler past `objdump` | **Deferred, not refused.** ADR-0008 defers the per-Category tool inventory to a later version, and #99 measured nothing here: the four Challenges ever attempted sit in the three most reachable Categories. Boot2Root's 9 wanting a raw-socket client is the strongest case and is the one to take up first. |
| `qemu-user-static` | The only entry that is not a list decision. We compete from an `aarch64` image, so an x86-64 Pwn or Reversing target cannot be *run* locally whatever is installed. That is an architecture question and it belongs with the Category inventory. |

## The picture, and what was proven before it was built

ADR-0014 has the model driving its own loop over a **text** stream, and a Challenge that *is* a
drawing reaches it as bytes however much tooling the image gains. Measured on *Blackboard*: the
model decoded a Dancing Men cipher from the PNG's byte structure, printed its answer through a
command so it graded `reproduced` — the whole verification ladder working exactly as designed — and
was wrong, because it was inferring a drawing it could not see. That is the worst shape a failure
can take here: confident, verified, and false.

#99 flagged the fix as untested, and it was right to — the CLI carrying an `-i/--image` flag is not
the model reading what it is handed. So it was tested first, in the built image on 27 August 2026: a
PNG with `VIOLET-7413-HARP` rendered into its pixels, attached with `-i`, against
`gpt-daybreak-blue-latest`. It came back `VIOLET-7413-HARP`, exactly, **having run no shell command
at all** — and the string appears nowhere in the file's bytes, so there was nothing to `strings`.

The failure modes were measured too, because the one that would have stopped this is a CLI that
refuses to start on a file it dislikes and takes the whole Attempt with it. It does not: a text
file, a truncated zip, three images at once, a 4000x4000 PNG and a path that does not exist all left
`codex exec` at exit 0 with the turn intact. **A misjudged picture costs context and never an
Attempt**, which is what makes this safe to wire on a trigger rather than behind a flag.

The trigger is recon's, not a new one. The cascade already dispatches on `file -b --mime-type` and
already has an `image` branch, so *which artefacts are pictures* is a fact it computes and used to
throw away; `Recon.pictures` now names it and `run_attempt(images=…)` attaches it. Dispatch is on
content, so a `.png` that is really a zip is not attached and a picture called `notes.dat` is. It is
capped at four — every attached picture is context spent before the model has read a word, and a
Challenge shipping a directory of frames would otherwise spend the opening frame on them.

## Consequences

- **The image is 1.28 GB and the build is slower.** Acceptable while it is built on the machine that
  competes; it would not be if we ever pulled it at a venue.
- **A tool that installs and misbehaves is still only caught if the probe exercises it.** `gsfonts`
  is the proof that this matters and equally the proof that the probe is not free — every future
  addition owes it a real-input assertion, or the layer stops meaning what it says.
- **Nothing here helps Pwn, Reversing or Mobile.** The 22% #99 reasons about is untouched by this
  record, and the table above is the list to work from when the Category inventory is taken up.
- **`pip` invites run-time installs that the venue network may refuse.** The mitigation is that the
  eleven are baked in; the residual is that a Run leaning on `pip` at 14:00 can still lose.
- **An Attempt on a picture Challenge costs more tokens than it did, once per turn.** ADR-0023 has
  the Attempt holding the turn loop and each turn is a fresh `codex exec` with no memory of the
  last, so the pictures are re-attached every turn for the same reason the prompt is re-composed —
  attaching them once would leave every turn after the first inferring the drawing again. The cap
  of four is therefore a per-turn cost and is a guess rather than a measurement: it is the first
  number to revisit once a picture Challenge has been replayed.

## Revisit when

- **A picture Challenge is replayed end to end.** That is when the cap of four, and whether the
  prompt should say a picture is attached, stop being guesses.
- **The Category tool inventory is taken up**, which is where every row of the declined table
  belongs and where `qemu-user` and the `aarch64` question have to be answered together.
- **A Run leans on `pip` and the venue network refuses it.** Once is bad luck; twice means the long
  tail wants baking in too.
- **The model id changes.** Image reading was proven for `gpt-daybreak-blue-latest` and for no other
  id, and which ids a subscription serves is account state that has already moved twice.
