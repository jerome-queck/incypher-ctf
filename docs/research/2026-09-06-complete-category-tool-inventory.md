# The complete per-Category tool inventory

_Research snapshot: 6 September 2026. This answers [The complete per-Category tool
inventory](https://github.com/jerome-queck/incypher-ctf/issues/183); it does not change the image.
“Must” means bake into the next Category-capability layer. “Useful” means keep available to a
bounded, explicit run-time install path or reconsider after a real reach. Local models are excluded._

## Decision-grade answer

The open core is **eighteen packages, grouped into seventeen capability rows**: `gdb-multiarch`,
`python3-pwntools`, `qemu-user`, `radare2`, `patchelf`, `strace`, `ltrace`, `jadx`, `apktool`,
`tshark`, `tesseract-ocr`, `ffmpeg`, `steghide`, `7zip`, `sqlite3`, `python3-pycryptodome`, and
`python3-sympy`, plus `netcat-openbsd`. Together they open the presently weak Pwn, Reversing, Mobile and protocol-
Forensics surfaces while filling common Crypto, image/audio and archive/database gaps.

This does **not** reopen ADR-0024. Its twelve landed packages and image attachment remain the
general-purpose floor. The new list begins where that measured verdict stops. It also does not
claim every CTF domain can be preinstalled: challenge-specific mathematics, filesystems,
geospatial stacks, memory profiles and proprietary formats stay on the long tail.

Three boundaries decide the list:

1. **Parse inert bytes by default.** Static parsers run with a timeout, input-size/output caps and
   a disposable output directory. They do not need extra Linux capabilities.
2. **Executing a Challenge is a separate capability.** GDB, QEMU, tracing and pwntools may execute
   hostile code. They run only inside the Challenge container boundary, against copied artefacts,
   never with `/var/run/docker.sock`, host credentials or a broader host mount. This image still
   invokes Codex at `danger-full-access`; package presence is not an inner sandbox.
3. **No privileged discovery surface.** Live packet capture, full-system/Android emulation,
   instrumentation servers and broad network scanners require privileges, target images or attack
   authority the generic Board does not establish. Offline PCAP decoding and ordinary TCP clients
   are in; raw-socket discovery and GUI viewers are out.

## Category coverage

| Expected Category | Baked floor already present | Open must-have capability | Useful long tail / explicit exclusion |
| --- | --- | --- | --- |
| Onboarding | `curl`, `wget`, `git`, image/PDF handling, attached images | OCR and media stream inspection | No separate suite; it composes the general floor |
| Crypto | Python, OpenSSL, `xxd` | PyCryptodome and SymPy | Z3 useful; SageMath and cracking rigs remain challenge-specific |
| Web | `curl`, Requests, `git`, `jq`, Python | None | `ffuf` useful after an endpoint hypothesis; SQLMap and browser proxies are not defaults |
| Pwn | ELF primitives from binutils | multiarch GDB, pwntools, QEMU user-mode, patching and syscall/library traces | `socat` useful; no kernel/VM escape laboratory |
| Reversing | `file`, `strings`, `objdump`, binwalk | radare2 plus the Pwn execution/trace set | Ghidra is capable but too large for the core; Mono/WABT/UPX are format-specific |
| Forensics | exiftool, binwalk3, scalpel, Sleuth Kit, PIL, ImageMagick, Poppler, zsteg | TShark, OCR, FFmpeg, Steghide, 7-Zip, SQLite | Scapy/YARA useful; live capture and Volatility profiles are not generic |
| Mobile | unzip, strings, images | JADX and Apktool (static APK/DEX/resources) | ADB useful only with a supplied endpoint; no emulator or Frida server |
| OSINT | web search, HTTP, attached images, exiftool | OCR and media inspection | WHOIS/DNS clients useful; no account-bound GUI or scraping browser by default |
| Misc | general floor | Union of archive, SQLite, media, static reversing and math primitives | WABT/Mono/Z3 after format detection |
| Boot2Root | HTTP clients and SSH-capable Python libraries | ordinary stream clients through Netcat and pwntools | SMB/DNS clients useful when a host is supplied; Nmap is currently unavailable; no raw sweep |

The four promoted Runs attempted only reachable categories, but still spent eighteen shell Steps
on missing commands and seven on missing imports; [ADR-0024](../adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)
records the exact reaches and their landed answer. The retained Brunner corpus supplies real APK,
PCAP, ELF, archive, image, BLE and save-file shapes. The untracked local snapshot under
`state/work/13` was spot-checked against the tracked Run inventories; exact hashes are recorded
below rather than disguised as portable repository links.
Its writeups demonstrate Python/AES, HTTP, image metadata and archive inspection, while a 2024
NahamCon Mobile writeup demonstrates JADX plus emulator/ADB, a 2025 Google CTF Pwn writeup
demonstrates cross-architecture QEMU plus GDB, and a 2025 BDSec author writeup demonstrates
radare2. Those writeups establish demand only; packaging, architecture and licence claims below
come from upstream and Kali.

## Core inventory: bake these

Costs are **incremental arm64 installed-size estimates**, not measured image deltas. I resolved
`Pre-Depends`/`Depends` from Kali rolling's official arm64 `Packages.gz` against the current
Dockerfile's named-package closure and summed `Installed-Size`. This is reproducible metadata, but
it omits maintainer-script/cache growth and may overcount files supplied by the actual base. The
pinned runtime is presently `Broken, not Running`, so an image build and `du` measurement remains
the implementation ticket's job. Every package below is present for both arm64 and amd64 in the
6 September indexes unless stated otherwise.

| Status / Categories | Package; purpose | Arch; estimated cost; material runtime dependencies | Licence | Security boundary; real-input capability probe |
| --- | --- | --- | --- | --- |
| **Must** · Pwn, Rev | [`gdb-multiarch`](https://pkg.kali.org/pkg/gdb) — debug ELF for more targets than the host GDB | arm64 + amd64; **69 MiB / 17 packages**; `gdb`, Python, debuginfod/ELF libraries | GPL-3.0-or-later | **Executes hostile code / ptrace.** Container only, timeout and copied fixture. Two-part probe: statically load a checked-in x86-64 fixture ELF on either host and assert `info files` plus a decoded instruction; separately `starti` the image's native `/bin/true` to prove ptrace without an architecture mismatch. QEMU's row owns foreign execution. |
| **Must** · Pwn, Boot2Root | [`python3-pwntools`](https://pkg.kali.org/pkg/pwntools) — ELF/checksec/ROP plus uniform local, TCP, SSH and serial tubes | `all`; **88 MiB / 29**; Capstone, Unicorn, ROPgadget, Paramiko, PyELFTools | MIT | **Executes/connects.** Challenge endpoints only; official submission remains outside the tool. Probe: launch a local echo server, round-trip `flag{probe}` through `pwnlib.tubes.remote`, then parse a checked-in fixture ELF with `ELF()` and build one `ROP()` gadget. |
| **Must** · Pwn, Rev | [`qemu-user`](https://pkg.kali.org/pkg/qemu) — user-mode execution of foreign-architecture Linux binaries | arm64 + amd64; **456 MiB / 1**; statically packaged emulators | GPL-2.0-or-later | **Executes hostile code and translates guest syscalls.** Container only; no `qemu-system`, KVM or binfmt registration. Probe: run checked-in, source-and-hash-documented tiny x86-64 and AArch64 static fixture ELFs under the opposite host's user emulator; assert output. |
| **Must** · Rev, Pwn | [`radare2`](https://pkg.kali.org/pkg/radare2) — scriptable headless disassembly, functions, strings and JSON analysis | arm64 + amd64; **45 MiB / 5**; `libradare2`, Capstone | LGPL-3.0-only | **Parser; debugger mode executes.** Core probe is static: `r2 -2qnc 'aaa;aflj'` on a checked-in fixture ELF, parse JSON and assert functions. Debug mode inherits the execution boundary. |
| **Must** · Pwn, Boot2Root, Web | [`netcat-openbsd`](https://tracker.debian.org/pkg/netcat-openbsd) — minimal raw TCP/UDP client and listener | arm64 + amd64; **0.4 MiB / 2**; libc/libbsd | BSD-3-Clause family | **Connects/listens.** Challenge or loopback endpoints only, timeout, no subnet scan. Probe: round-trip `flag{probe}` through a loopback listener and assert clean EOF. Pwntools remains the scripted/stateful client. |
| **Must** · Pwn, Rev | [`patchelf`](https://tracker.debian.org/pkg/patchelf) — inspect/change ELF interpreter and RPATH | arm64 + amd64; **0.3 MiB / 1**; libc/libstdc++ | GPL-3.0-or-later | **Mutates copies only.** Probe: copy an ELF, print its interpreter, set an RPATH, print and compare without touching the held input. |
| **Must** · Pwn, Rev | [`strace`](https://tracker.debian.org/pkg/strace) + [`ltrace`](https://tracker.debian.org/pkg/ltrace) — syscall and dynamic-library traces | arm64 + amd64; **4.3 MiB / 3 combined**; libc, libelf | LGPL-2.1-or-later / GPL-2.0-or-later | **Executes hostile code / ptrace.** Container and timeout. Probe: trace a tiny program that opens a fixture and calls `puts`; assert the filename in `strace` and `puts` in `ltrace`. |
| **Must** · Mobile | [`jadx`](https://pkg.kali.org/pkg/jadx) — headless APK/DEX to Java-like source and decoded resources | package `all`; **646 MiB / 100** because `default-jre` pulls GUI Java/GTK; Java 11+ upstream | Apache-2.0 | **Untrusted ZIP/DEX parser.** Preserve JADX ZIP/XML safety limits; never set its disable-security variables. Probe a small checked-in, source-and-hash-documented APK with `jadx -d out`, asserting its manifest and known class output; spot-check `OneVoice.apk` only when the machine-local corpus is present. |
| **Must** · Mobile | [`apktool`](https://pkg.kali.org/pkg/apktool) — decode resources, manifest and smali; rebuild after an explicit patch | arm64 + amd64; **337 MiB / 81 alone**, but much of Java is shared with JADX; `aapt`, Android framework resources, headless JRE | Apache-2.0 | **Untrusted ZIP/XML parser; rebuild does not authorize execution/signing.** Decode the same checked-in APK, assert `AndroidManifest.xml` and smali/resources, rebuild the unchanged fixture and inspect it as ZIP. |
| **Must** · Forensics | [`tshark`](https://pkg.kali.org/pkg/wireshark) — offline PCAP dissection, fields, streams and display filters | arm64 + amd64; **186 MiB / 38**; Wireshark protocol libraries | GPL-2.0-or-later | **Offline parser only.** No dumpcap capabilities or live interfaces. Probe a build-generated UDP PCAP made from fixed hex by `text2pcap`, asserting frames, UDP fields and payload; spot-check the retained corpus PCAP only when present. |
| **Must** · Onboarding, Forensics, OSINT | [`tesseract-ocr`](https://tracker.debian.org/pkg/tesseract) — English OCR and orientation/script detection | arm64 + amd64; **64 MiB / 14**; Leptonica, ICU, English/OSD data | Apache-2.0 | **Image parser.** Pixel/input and timeout caps. Probe: render `flag{probe}` with the already-installed font/ImageMagick stack, OCR it with `tesseract ... stdout`, normalize and assert the text. |
| **Must** · Forensics, OSINT, Misc | [`ffmpeg`](https://tracker.debian.org/pkg/ffmpeg) — decode/demux audio, video and obscure media; extract streams/frames | arm64 + amd64; **400 MiB / 141**; codec/format/filter libraries | LGPL-2.1-or-later; Kali build may enable GPL components | **Large untrusted codec surface.** One input, no network protocols, timeout/output caps. Probe: generate a one-second WAV and MP4 at build time, run `ffprobe -show_streams` and extract one frame/audio stream; assert type and duration. |
| **Must** · Forensics | [`steghide`](https://tracker.debian.org/pkg/steghide) — extract/test JPEG/BMP/WAV/AU embedded data | arm64 + amd64; **0.9 MiB / 3**; libmcrypt/libmhash | GPL-2.0-or-later | **Parser/writer; copies only.** Probe: embed `flag{probe}` into a generated WAV with a fixed passphrase, extract to a new directory, compare bytes. |
| **Must** · Forensics, Misc | [`7zip`](https://tracker.debian.org/pkg/7zip) — formats beyond current unzip/bzip/xz/zstd, including 7z | arm64 + amd64; **6.8 MiB / 1** | LGPL-2.1-or-later plus unRAR restriction in bundled code | **Archive bomb/path traversal risk.** List first; cap expanded bytes/files; disposable directory. Probe: make encrypted and nested 7z fixtures, list, extract with known password, compare payload and reject an over-budget fixture. |
| **Must** · Forensics, Mobile, Misc | [`sqlite3`](https://tracker.debian.org/pkg/sqlite3) — query browser/app/save databases without writing Python glue | arm64 + amd64; **2.2 MiB / 1**; existing SQLite library | Public domain | **Open held DB read-only/immutable.** Probe: create a DB containing `flag{probe}`, reopen with `file:...?...immutable=1`, query and assert value while the input hash stays fixed. |
| **Must** · Crypto, Forensics, Mobile | [`python3-pycryptodome`](https://tracker.debian.org/pkg/pycryptodome) — AES/RSA/hash primitives used by the retained corpus | arm64 + amd64; **5.6 MiB / 1**; Python | BSD-2-Clause | **Pure computation over bytes.** Probe: AES-GCM encrypt/decrypt `flag{probe}`, verify tag, and assert a modified tag fails. |
| **Must** · Crypto, Misc | [`python3-sympy`](https://tracker.debian.org/pkg/sympy) — symbolic algebra, modular arithmetic, factoring and equation solving | package `all`; **28 MiB / 2**; mpmath | BSD-3-Clause | **Pure computation; enforce time/memory limits on adversarial expressions.** Probe: solve a modular inverse/CRT fixture and substitute the result to recover `flag{probe}` bytes. |

`jadx` and `apktool` share a Java closure, so their row costs cannot be added. The metadata-derived
combined core is **1.81 GiB / 348 incremental packages** on arm64; JADX plus Apktool together are
**736 MiB**, not the sum of their rows. Shared-file/config growth still makes this an estimate.
Before implementation, install the proposed set together on the pinned
base and measure `docker image inspect`, `dpkg-query` and build time on **both** architectures.

## Useful, redundant, unsafe and unsupported

These are explicit declines, not claims that the tools lack value.

| Verdict / Categories | Package or capability; purpose | Arch; estimated cost; runtime dependencies; licence | Boundary and real-input admission probe |
| --- | --- | --- | --- |
| **Useful** · Forensics, network | `python3-scapy` — packet construction and uncommon-protocol parsing | `all`; **9 MiB / 1**; Python; GPL-2.0-only | Admit after a PCAP/protocol reach. Offline probe reads the retained PCAP and counts layers; packet transmission is disabled. TShark covers the common decode floor. |
| **Useful** · Pwn, Boot2Root | `socat` — bidirectional stream/PTY relay | arm64 + amd64; **2 MiB / 2**; OpenSSL/libwrap; GPL-2.0-only | Network/process tool, endpoint allowlist and timeout. Probe a local TCP echo and clean EOF. Pwntools already owns the normal client path. |
| **Useful** · Crypto, Rev | `python3-z3` — SMT solving | arm64 + amd64; **27 MiB / 3**; `libz3`; MIT | Pure computation but solver resource caps required. Probe a small bit-vector key schedule. Add after a real constraint-solving reach. |
| **Useful** · Rev, Misc | `wabt`, `mono-runtime`, `upx-ucl` — WebAssembly, managed .NET, packed-ELF specifics | both; **20 MiB / 1**, **58 MiB / 11**, **2.6 MiB / 1**; Apache-2.0, MIT, GPL-2.0-or-later with UPX exception | Format-triggered only. Probe `wasm-validate/wasm2wat`, run a compiled hello assembly under Mono, and pack/test/unpack a copied ELF. Mono/UPX execute or rewrite challenge material. |
| **Useful** · Crypto | `john` — CPU password/hash recovery | both; **78 MiB / 4** on arm64; OpenSSL/OpenMP; GPL-2.0-or-later | Bounded wordlist/time only. Probe crack one known tiny salted hash. Prefer over `hashcat` in the generic image. |
| **Useful but costly** · Crypto | `hashcat` — OpenCL/GPU-style cracking | both; **541 MiB / 34**; PoCL/LLVM/OpenCL; MIT | The run-day arm64 VM has no established GPU pass-through. Probe CPU backend against one tiny known hash before admission. Otherwise John/Python are smaller. |
| **Useful** · Forensics | `yara` — signature matching | both; **1.3 MiB / 2**; `libyara`; BSD-3-Clause | Static scan only. Probe a rule matching a generated byte sequence. It classifies known patterns; it does not inspect structure like the current floor. |
| **Useful** · Boot2Root, OSINT | `smbclient`, `bind9-dnsutils`, `whois` — supplied-host SMB/DNS/registration clients | both; **81 MiB / 30**, **7 MiB / 13**, **0.5 MiB / 1**; GPL-3+, MPL-2.0, GPL-2+ | Network allowlist; no subnet enumeration. Probes need disposable local SMB/DNS fixtures; WHOIS needs a recorded response because build-time Internet is not a probe. |
| **Useful** · Mobile | `android-sdk-platform-tools` — ADB client for a supplied, authorized device/emulator endpoint | both; **11 MiB / 29**; Android tools, filesystem/graph utilities; Apache-2.0/BSD mix | No server/device is baked and USB discovery is disabled. Admit only when the Challenge supplies an endpoint; probe against a disposable emulator fixture, list one package, stream bounded logcat, then prove disconnect/teardown. |
| **Useful** · Web | `ffuf` — bounded content discovery | both; **9 MiB / 1**; libc; MIT | Only against the Challenge origin, explicit request/rate cap. Probe a local HTTP fixture with one hidden path. |
| **Redundant** · Web | `gobuster` — duplicate directory/DNS discovery beside ffuf | arm64 + amd64; **8.6 MiB / 1**; libc; Apache-2.0 | Same Challenge-origin/rate boundary. Probe both against the same local hidden-path fixture and assert the same discovery; retain ffuf because one client is enough. |
| **Useful but unsafe by default** · Web | `sqlmap` — automated SQL injection exploration | `all`; **13 MiB / 2**; Python; GPL-2.0-or-later | Generates attack traffic and can mutate data. Admit only after a SQLi hypothesis, Challenge-origin scope and non-destructive flags; local vulnerable fixture probe. Never generic recon. |
| **Useful but too costly** · Rev | `ghidra` — high-quality decompiler/headless analysis | both Kali packages; **1.44 GiB / 100** incremental on arm64; full OpenJDK 21 JDK/GUI closure; Apache-2.0 with separately licensed components | Static headless mode only, timeout/disposable project. Probe `analyzeHeadless` on two real ELFs and export decompilation. Reconsider if radare2 results establish a decompiler gap worth the cost. |
| **Redundant** · Forensics | `foremost` — file carving already owned by Scalpel | arm64 + amd64; **0.2 MiB / 1**; libc; GPL-2.0-or-later | Static parser, disposable output and carve limits. Probe both carvers against one generated blob containing a JPEG and assert the same payload recovery; retain the already-baked Scalpel. |
| **Redundant** · Forensics | `tcpdump` — offline PCAP summary already owned more deeply by TShark | arm64 + amd64; **24 MiB / 5**; libpcap plus systemd helpers; BSD-3-Clause | Offline `-r` only; no capture capability. Probe the generated PCAP and assert packets, then require TShark to decode a named protocol field tcpdump does not expose. |
| **Unsupported viewer** · Forensics | `wireshark` — Qt GUI over the TShark/Wireshark protocol libraries | arm64 + amd64; **691 MiB / 207** against the current base; Qt6/GTK/media plus Wireshark libraries; GPL-2.0-or-later | No display/session controller exists. Negative real-input probe: with `DISPLAY`/Wayland unset, opening the generated PCAP must not yield an operable result while `tshark -r` succeeds. Image/media/PDF viewing is already supplied by model attachment and headless extraction. |
| **Unsupported now** · Boot2Root | `nmap` — port/service discovery | package absent from both official rolling indexes fetched on 6 Sep; installed cost and dependencies therefore unavailable; upstream Nmap Public Source License | Do not invent a curl-installed binary. Python/pwntools can test a small supplied port set. Admission probe, if packaging returns: scan a loopback fixture with two known ports and a strict rate/port list. Raw scans also need an explicit target/rate boundary. |
| **Unsafe / unsupported** · Mobile | `android-sdk` plus a downloaded system image and Frida server — dynamic APK instrumentation | SDK is both-arch, **605 MiB / 145** before a multi-GiB architecture/API system image; Java, Android build/platform tools, QEMU/KVM/device surface; Apache/GPL mix, Frida wxWindows Library Licence | A generic APK does not supply an authorized device image. Admission probe belongs to a separate disposable Instance: boot pinned image, install a fixture APK, attach Frida, observe one known method, then prove egress/credential isolation and teardown. Static JADX/Apktool remains the core. |
| **Unsafe / unsupported** · Rev | `wine64` — execute Windows PE | both index architectures; **685 MiB / 104** on arm64 before any x86 translation/guest stack; Wine/media/graphics libraries; LGPL-2.1-or-later | Executes foreign code and on arm64 does not itself solve the expected x86-64 target gap. Admission probe: run a source-and-hash-documented PE in a separately bounded Instance and prove filesystem/egress isolation. |
| **Unsafe / unsupported** · Rev, Boot2Root | `qemu-system-x86` / `qemu-system-arm` — full-system emulation | both; **124 MiB / 36** and **133 MiB / 36** before guest images; QEMU common/data, firmware, slirp/storage libraries; GPL-2.0-or-later | Requires a supplied, authorized guest image and separately bounded storage/network lifecycle. Admission probe: boot a tiny pinned image, exchange `flag{probe}` over serial, then prove image rollback and process teardown. QEMU user-mode covers the generic ELF gap. |
| **Unsupported package; useful domain tool** · Forensics | Volatility 3 — memory-image framework | no `volatility3` package in either index; upstream Python install cost unmeasured; Python, symbols and format plugins; Volatility Software Licence 1.0 | Static but expensive/adversarial parsing. Admission probe: pin upstream and symbols, enumerate a known process from a licence-compatible sample memory image, verify its hash and cap time/output. |
| **Unsupported package; useful domain tool** · Rev | angr — symbolic execution | no `python3-angr` package in either index; upstream Python/native solver closure unmeasured; claripy/Z3, cle/pyvex/archinfo; BSD-2-Clause | May execute models and exhaust CPU/memory. Admission probe: solve a tiny source-and-hash-documented branch fixture under strict resources; add only after a real reach and pinned-source review. |
| **Unsupported** · all | Proprietary IDA/Binary Ninja/Burp editions, hosted viewers and account-bound OSINT clients | no redistributable unattended Kali package; architecture, install cost and dependencies vary by product; proprietary/account-specific licences | Licence, login and GUI/session ownership are outside the Solver image. Negative admission probe: a clean credential-free container cannot open a supplied fixture through the product. The model can use text/attached-image evidence and allowed web search; it cannot inherit the Owner's desktop session. Local models are excluded, not classified. |

## Why this is the closed expected surface

“Complete” here means every **recurring input or interaction shape in the expected Categories** has
one low-friction primitive, not that every named CTF tool is installed:

- bytes, archives, filesystems, images and PDFs already had a floor; 7-Zip, OCR, media streams,
  SQLite, steghide and PCAP fill observed format holes;
- native executables gain static analysis, debugging, tracing, patching and foreign-user-mode
  execution without introducing a whole guest OS;
- APK/DEX gains two complementary static views without executing an app;
- ordinary request/response network work uses curl, Requests and pwntools; offline protocol work
  uses TShark; privileged discovery stays outside the default authority;
- common cryptographic and symbolic operations gain stable Debian modules; specialised CAS,
  solvers and crackers remain reach-triggered.

The package list must still be **adaptive**. Modern agent evidence says a broad, queryable Kali
environment improves aggregate solving, but category effects differ and tool names alone do not
create usable capability. Each baked line therefore has an executable real-input probe. A future
Run reach can promote a Useful item; an unused core item can be challenged after Category-complete
practice Runs record its cost and benefit.

## Implementation handoff

One build ticket can implement the proposed core, but it should split validation by
boundary:

1. add packages one per line with per-line reasons, preserving ADR-0008/0024 conventions;
2. measure the **combined** layer on arm64 and amd64, including build time and final image size;
3. keep parser probes in the Docker build where fixtures are cheap and licence-compatible;
4. put execution/network probes in a no-network or loopback-only script run by CI and the pinned
   runtime, with time/memory/output caps;
5. prove a foreign ELF on each host architecture, a real APK, the retained PCAP, and generated
   OCR/media/archive/SQLite/crypto fixtures;
6. separately decide whether the 456 MiB QEMU and shared Java closures remain affordable after
   measured image deltas. If not, split a deterministic secondary Category layer; do not restore
   run-time Internet as an unrecorded dependency.

## Newly surfaced Wayfinder decisions

Three questions are now sharp enough for tickets; none is resolved by this inventory:

- **The measured Category-layer budget** — after one combined arm64/amd64 build, is the full core
  affordable in the one run-day image, or should Java/QEMU live in a deterministic secondary
  Category layer?
- **The dynamic-target execution boundary** — will Mobile/Windows/full-system dynamic analysis be
  supported at all, and if so, what disposable Instance owns emulator images, privileges, egress,
  credentials and teardown?
- **The adversarial-parser boundary** — before adding the larger parser surface, is the current
  outer container alone sufficient, or must parsers and executed Challenge code move behind a
  separate uid/seccomp/resource boundary?

The transient Nmap absence stays fog unless practice-board evidence shows that bounded supplied-
host probing is inadequate; only then is a packaging/pinning decision worth a ticket.

## Evidence and reproducibility

### Repository evidence

- [Issue #99](https://github.com/jerome-queck/incypher-ctf/issues/99) and
  [ADR-0024](../adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)
  are the settled nine-command/seven-import verdict. Promoted raw traces are
  [`brunner-gate-1`](../../runs/brunner-gate-1.jsonl),
  [`brunner-gate-2`](../../runs/brunner-gate-2.jsonl),
  [`brunner-gate-3`](../../runs/brunner-gate-3.jsonl), and [`v1-gate`](../../runs/v1-gate.jsonl).
- The ignored local corpus under `state/work/13` is **machine-local evidence**, not a portable
  citation. The three inspected fixtures were `OneVoice.apk` SHA-256
  `c9ff86e15cf9bd35173bf5ee01ab2d4d12961f7bffe9c1fa27786a344427704a`,
  `the-missing-recipe.pcap` SHA-256
  `91ff7f1d5edbb391ac56a42da72c88d07d77599b7eec7dbecf87a9db1f9629b4`, and
  `go_go_budgetmaster` SHA-256
  `21ee9655a76675ee5611bdf49c6a83c31e3d15a0f49ecf8205359251d7bb578a`. Their Category,
  filenames and held-byte sizes remain discoverable in the tracked promoted Run inventories;
  the public [BrunnerCTF 2026 event page](https://ctftime.org/event/3065) is the acquisition-
  provenance pointer. Neither source promises permanent file re-download.
- [Modern autonomous CTF research](2026-09-05-modern-autonomous-ctf-solving.md) supplies the
  environment/usability evidence and its limits.

### Packaging snapshot

- Kali rolling official indexes fetched 6 September 2026:
  [arm64 `Packages.gz`](https://kali.download/kali/dists/kali-rolling/main/binary-arm64/Packages.gz),
  SHA-256 `ab1fc678f86aa1b62b497a5aff750e6c8bc5215ea162430d85b22529da8d6734`;
  [amd64 `Packages.gz`](https://kali.download/kali/dists/kali-rolling/main/binary-amd64/Packages.gz),
  SHA-256 `514c4d8575222a3cdfc44166d31bafddaa18b3d7017074b4a198738cf598f40b`.
- Kali package trackers linked in the tables establish current source-package presence. Debian
  trackers are used where Kali consumes the Debian package unchanged. Architecture, package
  payload size, direct dependencies and the closure estimates come from the two indexes, not from
  tracker prose.

### Primary upstream capability, architecture and licence sources

- [Pwntools getting started](https://docs.pwntools.com/en/stable/intro.html) and
  [ROP documentation](https://docs.pwntools.com/en/stable/rop/rop.html);
  [QEMU user-mode documentation](https://www.qemu.org/docs/master/user/main.html);
  [GDB copying](https://sourceware.org/git/?p=binutils-gdb.git;a=blob;f=COPYING3).
- [radare2 repository/licence](https://github.com/radareorg/radare2),
  [Patchelf repository/licence](https://github.com/NixOS/patchelf),
  [strace repository/licence](https://github.com/strace/strace), and
  [ltrace repository/licence](https://gitlab.com/cespedes/ltrace).
- [JADX README/licence and safety switches](https://github.com/skylot/jadx),
  [Apktool documentation](https://apktool.org/docs/), and
  [Apktool licence](https://github.com/iBotPeaches/Apktool/blob/master/LICENSE.md).
- [TShark manual](https://www.wireshark.org/docs/man-pages/tshark.html),
  [Tesseract user manual](https://tesseract-ocr.github.io/tessdoc/), and
  [FFmpeg legal/licence page](https://ffmpeg.org/legal.html).
- [7-Zip licence](https://www.7-zip.org/license.txt),
  [SQLite copyright](https://www.sqlite.org/copyright.html),
  [PyCryptodome licence](https://github.com/Legrandin/pycryptodome/blob/master/LICENSE.rst), and
  [SymPy licence](https://github.com/sympy/sympy/blob/master/LICENSE).
- [Ghidra headless analyzer](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/RuntimeScripts/support/analyzeHeadlessREADME.md),
  [supported native platforms](https://github.com/NationalSecurityAgency/ghidra/blob/master/GhidraDocs/GettingStarted.md),
  and [licensing notice](https://github.com/NationalSecurityAgency/ghidra/blob/master/NOTICE).
- [Nmap Public Source Licence](https://nmap.org/npsl/),
  [Gobuster repository/licence](https://github.com/OJ/gobuster),
  [Wine licensing](https://gitlab.winehq.org/wine/wine/-/wikis/Licensing),
  [Frida licence](https://github.com/frida/frida/blob/main/COPYING),
  [Volatility 3 licence](https://github.com/volatilityfoundation/volatility3/blob/develop/LICENSE.txt),
  and [angr repository/licence](https://github.com/angr/angr).

### Representative modern writeups (demand only)

- [NahamCon CTF 2024 Mobile](https://blog.ikuamike.io/posts/2024/nahamcon_ctf_2024_mobile/)
  uses JADX, an Android emulator and ADB/logcat.
- [Google CTF 2025, The Classic Notes App](https://utkar5hm.github.io/posts/googlectf25-the-classic-notes-app/)
  uses QEMU AArch64 and GDB across an architecture boundary.
- [BDSec CTF 2025, revME](https://kshackzone.com/ctfs/writeups/NomanProdhan/70/bdsec-ctf-2025/reverse-engineering/revme)
  is the challenge author's radare2-based solution.

## Uncertainties that remain

- Combined image delta and build time are metadata estimates until Colima is healthy and both
  architecture builds run. Java/QEMU cost could justify a deterministic secondary layer.
- The official 2026 Category names and file/protocol distribution are not yet released. This is a
  coverage decision over the known expected Categories, old corpus and current Runs, not a claim
  about unseen Challenges.
- Nmap's tracker/index disagreement may be transient. It needs a packaging/pinning decision only
  if a practice Board proves supplied-host probing insufficient.
- The correct boundary for dynamic Android and Windows targets is a separate Instance/emulator
  design. Adding packages to this container cannot settle it.
- Parser exposure should be threat-modelled before landing the layer: JADX, Apktool, FFmpeg,
  TShark, archive and image parsers all consume adversarial bytes inside the outermost current
  boundary.
