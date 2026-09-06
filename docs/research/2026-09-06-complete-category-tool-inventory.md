# The complete per-Category tool inventory

_Research snapshot: 6 September 2026. This answers [The complete per-Category tool
inventory](https://github.com/jerome-queck/incypher-ctf/issues/183); it does not change the image.
“Must” means v2 must provide the capability; it does **not** freeze the exact package, layer or
trust boundary. “Useful” means v2 must preserve a bounded on-demand seam when evidence detects the
format/Category. [Issue #186](https://github.com/jerome-queck/incypher-ctf/issues/186) owns exact
production composition, locks, probes and manifest. Local models are excluded._

## Decision-grade answer

Specify a **maximum-strength classic surface**, not an eighteen-package minimum. The eighteen measured
packages below are the common substrate. Deep capability profiles cover Crypto, Web, Pwn, Reversing,
Forensics, Stego, OSINT and Misc. Open-string fallback Categories get a compact recognizer/client
baseline plus ADR-0024's bounded on-demand path unless organiser or corpus evidence raises them.
Issue #186 may decide package and layering; it cannot silently remove a distinct capability inside
the priority set.

This does **not** reopen ADR-0024. Its twelve landed packages and image attachment remain the
general-purpose floor. The new list begins where that measured verdict stops. It also does not
claim every target can be executed in the common container: target images, credentials and
privileges belong to separately governed profiles. Proprietary/account-bound tools remain out.

Three boundaries decide the list:

1. **Parse inert bytes by default.** Static parsers run with a timeout, input-size/output caps and
   a disposable output directory. They do not need extra Linux capabilities.
2. **Executing a Challenge is a separate capability.** GDB, QEMU, tracing and pwntools may execute
   hostile code. They run only inside the Solver container boundary, against copied artefacts,
   never with `/var/run/docker.sock`, host credentials or a broader host mount. This image still
   invokes Codex at `danger-full-access`; package presence is not an inner sandbox.
3. **Network and target authority follows the Challenge.** Offline decoding is always available.
   Scanner capability may address only the Challenge Target under Board-derived rate and request
   caps. Android/full-system/Wine/Frida paths require the separate dynamic-target boundary;
   package presence never grants target, credential or device authority.

## Category coverage

| Open Category string | Must-have v2 capability | Authority-gated / format-triggered complement |
| --- | --- | --- | --- |
| Onboarding | Existing HTTP/git/file/image/PDF floor; OCR, media, archives and SQLite | None: it composes the substrate |
| Crypto | PyCryptodome, SymPy, SageMath/fpylll, Z3/cvc5, gmpy2, common factoring/Coppersmith paths, John | Hashcat or specialised lattice engines only when their distinct backend applies |
| Web | curl/Requests plus ffuf, feroxbuster, nuclei, sqlmap, SSTImap, jwt_tool, Arjun, smuggling clients, Katana/httpx, Playwright/Chromium and wordlists | Every active request stays on the Challenge origin and within a durable request/rate envelope |
| Pwn | Compiler toolchain, pwntools, multiarch GDB+GEF, ROP tools, QEMU user, patchelf, traces, seccomp tools, pwninit/glibc corpus, AFL++/honggfuzz | QEMU-system/kernel kit when a kernel or guest image is supplied |
| Reversing | radare2 **and Ghidra headless**, Capstone/Unicorn/Keystone, angr/Z3, GoReSym, WABT, .NET+ilspycmd, UPX | Qiling, Wine or a full guest for formats requiring execution |
| Forensics | TShark, Scapy, Zeek, Sleuth Kit, Volatility3+symbols, carvers, binwalk, PCredz, YARA, Office/PDF tools, John, media/OCR/archive/SQLite | Live capture is disabled unless the Challenge explicitly supplies an interface |
| Stego | zsteg, steghide/stegseek, stego-lsb/stegoveritas, exiftool, ImageMagick, FFmpeg, Tesseract, barcode, SSTV and RF/audio decoders | GDAL remains ADR-0024's bounded domain-library path; large ML checkpoints remain excluded |
| Mobile | JADX, Apktool and Android platform tools | Frida, emulator/system image and app execution live behind the dynamic-target boundary |
| OSINT | Search/HTTP/browser, EXIF/OCR/geospatial libraries, DNS/WHOIS plus Maigret/Sherlock/holehe/theHarvester | Public-Internet tools use explicit scope, rate limits and no inherited accounts |
| Hardware/RF | Compact baseline: binwalk and generic audio/serial/PCAP decoding | sigrok-cli, multimon-ng, minimodem, rtl_433 and specialised decoders on detected capture; no physical-device authority |
| Blockchain | Compact baseline: Python ABI/RPC parsing | Foundry, heimdall, Medusa, Halmos and chain SDK profile after EVM/Solana/Move detection; no paid explorer API |
| Game | Reuse Rev/Misc: Ghidra, WABT, webcrack, .NET/ilspycmd | engine-specific extractors and Frida/scanmem after format detection and boundary admission |
| AI/ML | Compact baseline: fickling, picklescan, modelscan and safe tensor/model structure inspection | ART/torchattacks/garak/pyrit after task detection; local inference models remain excluded |
| Cloud | Compact baseline: JSON/YAML/JWT and HTTP | AWS/GCP/Azure CLIs/SDKs only after provider evidence; no host credentials, metadata endpoint or ambient account |
| Programming/algorithmic | Reuse Crypto/Misc: compiler toolchain, Python/Node, Z3/cvc5 and Sage | Go/Rust/OR-Tools/PySAT/MiniZinc profile after language/problem detection |
| Jail/sandbox | Reuse Pwn/Misc: pyjailbreaker, seccomp-tools, traces and language runtimes | Copied, timed execution only; no Solver/host namespace escape authority |
| Networking/protocol | Reuse Web/Pwn/Forensics: Netcat, pwntools, SSH, DNS/WHOIS and TShark/Scapy | socat, SMB and bounded Nmap-equivalent discovery only for supplied Target addresses |
| Boot2Root | Reuse Web/Pwn/Forensics plus supplied-host clients | Full guest/kernel profile only for an owned Challenge Instance |
| Misc/unknown | Union is queryable: archives, encodings/CyberChef, barcodes, media, math, RE, Qiling and file identification | Unknown Category never suppresses Intake or routing; file/protocol evidence selects profiles |

`healthcare` is deliberately absent. [`CONTEXT.md`](../../CONTEXT.md) defines it as a **Scenario
setting**, not a Category; it may improve interpretation but never selects a package. Category and
CTFd type remain open strings, so this table is a coverage map rather than an enum.

The four promoted Runs attempted only reachable categories, but still spent eighteen shell Steps
on missing commands and seven on missing imports; [ADR-0024](../adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)
records the exact reaches and their landed answer. The retained Brunner corpus supplies real APK,
PCAP, ELF, archive, image, BLE and save-file shapes. The untracked local snapshot under
`state/work/13` was spot-checked against the tracked Run inventories; exact hashes are recorded
below rather than disguised as portable repository links.
Its writeups demonstrate Python/AES, HTTP, image metadata and archive inspection, while a 2024
NahamCon Mobile writeup demonstrates JADX plus emulator/ADB, a 2025 Google CTF Pwn writeup
demonstrates cross-architecture QEMU plus GDB, and a 2025 BDSec author writeup demonstrates
radare2. Those writeups establish demand only; packaging, architecture and licence claims in the
two measured tables immediately below come from upstream and Kali.

## Measured common substrate: bake these

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
combined substrate is **1.81 GiB / 348 incremental packages** on arm64; JADX plus Apktool together are
**736 MiB**, not the sum of their rows. Shared-file/config growth still makes this an estimate.
Before implementation, install the proposed set together on the pinned
base and measure `docker image inspect`, `dpkg-query` and build time on **both** architectures.

## Additional inventory and declines

These rows distinguish required capability, bounded long-tail use, equal-path redundancy and hard
exclusion. Package admission and the security boundary remain #186 decisions.

| Verdict / Categories | Package or capability; purpose | Arch; estimated cost; runtime dependencies; licence | Boundary and real-input admission probe |
| --- | --- | --- | --- |
| **Must** · Forensics, network | `python3-scapy` — packet construction and uncommon-protocol parsing | `all`; **9 MiB / 1**; Python; GPL-2.0-only | Offline probe reads a PCAP and asserts uncommon layers. Packet transmission is separately authority-gated. |
| **Useful** · Pwn, Boot2Root | `socat` — bidirectional stream/PTY relay | arm64 + amd64; **2 MiB / 2**; OpenSSL/libwrap; GPL-2.0-only | Network/process tool, endpoint allowlist and timeout. Probe a local TCP echo and clean EOF. Pwntools already owns the normal client path. |
| **Must** · Crypto, Rev | `python3-z3` — SMT solving | arm64 + amd64; **27 MiB / 3**; `libz3`; MIT | Pure computation but solver resource caps required. Probe a small bit-vector key schedule. |
| **Must** · Rev, Misc, Game | `wabt`, `mono-runtime`, `upx-ucl` — WebAssembly, managed .NET, packed-ELF specifics | both; **20 MiB / 1**, **58 MiB / 11**, **2.6 MiB / 1**; Apache-2.0, MIT, GPL-2.0-or-later with UPX exception | Probe `wasm-validate/wasm2wat`, run a compiled hello assembly, and pack/test/unpack a copied ELF. Mono/UPX execution or rewriting stays bounded. |
| **Must** · Rev | GoReSym — recover names/types from stripped Go binaries | arm64+amd64 upstream builds; **S estimated**; self-contained Go binary; Apache-2.0 | Static parser. Build a stripped, hashed Go fixture; assert recovered function/type names without executing the held binary. |
| **Must** · Crypto | `john` — CPU password/hash recovery | both; **78 MiB / 4** on arm64; OpenSSL/OpenMP; GPL-2.0-or-later | Bounded wordlist/time only. Probe one known tiny salted hash. |
| **Useful but costly** · Crypto | `hashcat` — OpenCL/GPU-style cracking | both; **541 MiB / 34**; PoCL/LLVM/OpenCL; MIT | The run-day arm64 VM has no established GPU pass-through. Probe CPU backend against one tiny known hash before admission. Otherwise John/Python are smaller. |
| **Must** · Forensics | `yara` — signature matching | both; **1.3 MiB / 2**; `libyara`; BSD-3-Clause | Static scan only. Probe a rule matching a generated byte sequence. |
| **Must** · Boot2Root, OSINT, network | `smbclient`, `bind9-dnsutils`, `whois` — supplied-host SMB/DNS/registration clients | both; **81 MiB / 30**, **7 MiB / 13**, **0.5 MiB / 1**; GPL-3+, MPL-2.0, GPL-2+ | Network allowlist. Probe disposable local SMB/DNS fixtures; replay a recorded WHOIS response. |
| **Must client** · Mobile | `android-sdk-platform-tools` — ADB client for a supplied, authorized device/emulator endpoint | both; **11 MiB / 29**; Android tools, filesystem/graph utilities; Apache-2.0/BSD mix | USB discovery disabled. Probe a disposable emulator fixture, list one package, stream bounded logcat, then prove disconnect/teardown. |
| **Must, gated use** · Web | `ffuf` — bounded content discovery | both; **9 MiB / 1**; libc; MIT | Only the Challenge origin, explicit request/rate cap. Probe a local HTTP fixture with one hidden path. |
| **Redundant** · Web | `gobuster` — duplicate directory/DNS discovery beside ffuf | arm64 + amd64; **8.6 MiB / 1**; libc; Apache-2.0 | Same Challenge-origin/rate boundary. Probe both against the same local hidden-path fixture and assert the same discovery; retain ffuf because one client is enough. |
| **Must, gated use** · Web | `sqlmap` — automated SQL injection exploration | `all`; **13 MiB / 2**; Python; GPL-2.0-or-later | Generates attack traffic and can mutate data. Use only after a SQLi hypothesis, Challenge-origin scope and non-destructive flags; probe a local vulnerable fixture. Never generic recon. |
| **Must** · Rev, Game | `ghidra` — high-quality headless decompiler and analysis platform | both Kali packages; **1.44 GiB / 100** incremental on arm64; full OpenJDK 21 JDK/GUI closure; Apache-2.0 with separately licensed components | Static headless mode, timeout/disposable project. Probe `analyzeHeadless` on two real ELFs and export decompilation. Cost may select a layer, not exclusion. |
| **Redundant** · Forensics | `foremost` — file carving already owned by Scalpel | arm64 + amd64; **0.2 MiB / 1**; libc; GPL-2.0-or-later | Static parser, disposable output and carve limits. Probe both carvers against one generated blob containing a JPEG and assert the same payload recovery; retain the already-baked Scalpel. |
| **Redundant** · Forensics | `tcpdump` — offline PCAP summary already owned more deeply by TShark | arm64 + amd64; **24 MiB / 5**; libpcap plus systemd helpers; BSD-3-Clause | Offline `-r` only; no capture capability. Probe the generated PCAP and assert packets, then require TShark to decode a named protocol field tcpdump does not expose. |
| **Unsupported viewer** · Forensics | `wireshark` — Qt GUI over the TShark/Wireshark protocol libraries | arm64 + amd64; **691 MiB / 207** against the current base; Qt6/GTK/media plus Wireshark libraries; GPL-2.0-or-later | No display/session controller exists. Negative real-input probe: with `DISPLAY`/Wayland unset, opening the generated PCAP must not yield an operable result while `tshark -r` succeeds. Image/media/PDF viewing is already supplied by model attachment and headless extraction. |
| **Unsupported now** · Boot2Root | `nmap` — port/service discovery | package absent from both official rolling indexes fetched on 6 Sep; installed cost and dependencies therefore unavailable; upstream Nmap Public Source License | Do not invent a curl-installed binary. Python/pwntools can test a small supplied port set. Admission probe, if packaging returns: scan a loopback fixture with two known ports and a strict rate/port list. Raw scans also need an explicit target/rate boundary. |
| **Useful, gated long tail** · Mobile | `android-sdk` plus pinned system image and Frida server — dynamic APK instrumentation | SDK both-arch, **605 MiB / 145** before a multi-GiB system image; Java, QEMU/KVM/device surface; Apache/GPL mix, Frida wxWindows Library Licence | Disposable execution sandbox only: boot image, install fixture APK, attach Frida, observe a known method, then prove egress/credential isolation and teardown. |
| **Useful, gated long tail** · Rev, Game | `wine64` — execute Windows PE | both index architectures; **685 MiB / 104** on arm64 before x86 translation/guest stack; Wine/media/graphics; LGPL-2.1-or-later | The arm64/x86-64 gap must be proved. Run a documented PE only inside a disposable execution sandbox and prove filesystem/egress isolation. |
| **Useful, gated long tail** · Rev, Boot2Root | `qemu-system-x86` / `qemu-system-arm` — full-system emulation | both; **124 MiB / 36** and **133 MiB / 36** before guest images; firmware/slirp/storage; GPL-2.0-or-later | Supplied/pinned guest only. Boot a tiny image, exchange `flag{probe}` over serial, prove rollback and teardown. |
| **Must from pinned upstream** · Forensics | Volatility 3 — memory-image framework | no Kali package; upstream Python closure and symbols, installed cost **unmeasured**; Volatility Software Licence 1.0 | Static adversarial parser. Pin upstream+symbols; enumerate a known process from a licence-compatible hashed sample under time/output caps. |
| **Must from pinned upstream** · Rev | angr — symbolic execution | no Kali package; upstream Python/native closure cost **unmeasured**; Claripy/Z3, CLE/PyVEX; BSD-2-Clause | CPU/memory bounded. Solve a documented branching ELF and verify the recovered stdin; pin and audit the source closure. |
| **Unsupported** · all | Proprietary IDA/Binary Ninja/Burp editions, hosted viewers and account-bound OSINT clients | no redistributable unattended Kali package; architecture, install cost and dependencies vary by product; proprietary/account-specific licences | Licence, login and GUI/session ownership are outside the Solver image. Negative admission probe: a clean credential-free container cannot open a supplied fixture through the product. The model can use text/attached-image evidence and allowed web search; it cannot inherit the Owner's desktop session. Local models are excluded, not classified. |

## Legacy full-suite crosswalk

The old `ctf-workspace` at commit `4631053` is a **candidate catalogue, not architecture**.
Its own `docs/tooling.md` says `sandbox/INSTALLED.md` wins; the Dockerfile contains best-effort
fetches, and its smokes range from six functional P2 probes to mostly command/import presence in
P2.5. Brunner commit `92e3399` contributes a smaller audited substrate and attempt-local
`uv --isolated` dependency pattern. The verdict below covers the candidate set in coherent
capability bundles. Cost bands are explicit estimates from the legacy manifest and runtime type:
**S** <50 MiB, **M** 50–250 MiB, **L** 250 MiB–1 GiB, **XL** >1 GiB; they are not current-image
measurements. Each named tool—not one representative per row—must pass the row's applicable real
fixture before the implementation may call it installed. Exact package locks replace mixed family
licence labels with each upstream notice; an unresolved notice blocks that artefact, not the
capability.

Where a row below lacks an inline upstream link, architecture/licence/maintenance details are
explicitly **legacy-workspace claims at commit `4631053`, not verified current facts**. They justify
candidate classification only. #186 must resolve each admitted artefact against its primary
upstream release, licence and package metadata; failed verification selects an equal-capability
replacement or leaves the bounded seam visible.

| Verdict / Categories | Package/distribution; purpose | Target arch; installed cost; runtime dependencies; licence | Security boundary; real-input probe |
| --- | --- | --- | --- |
| **Must** · general, programming | `build-essential`, GCC/G++, CMake, pkg-config; Python+`uv`, Node/npm — build/run common challenge code | both; **L estimated**; language runtimes/linkers; GPL/BSD/MIT/Apache family notices | Compile and run per-language hashed fixtures with network off; attempt-local caches only. |
| **Useful, language-triggered** · programming | Go and Rust toolchains | both; **L estimated**; compilers/linkers/package metadata; BSD/MIT/Apache notices | Compile and run a hashed fixture for the detected language with network off and an attempt-local cache. |
| **Must** · Crypto, algorithmic | SageMath, fpylll, gmpy2, Z3, cvc5, OR-Tools, PySAT, MiniZinc, prime/factoring and maintained Coppersmith libraries | both where native wheels/packages exist; **XL estimated**; Python/CAS/native solver closures; GPL/LGPL/MIT/BSD/MPL notices by project | Each solver gets its own lattice, SAT, CP-SAT, integer or CRT fixture under CPU/RAM/deadline caps. |
| **Must** · Pwn, Rev | ROPgadget, ropper, Capstone/Unicorn/Keystone, angrop, GEF, pwninit, glibc-all-in-one, one_gadget, seccomp-tools | mostly both; **M estimated**; Python/Ruby/GDB/glibc; GPL/MIT/BSD notices by project | Each parser/helper resolves its documented output from a real ELF/libc/seccomp fixture; execution/ptrace only in copied workspace. |
| **Must** · Pwn | AFL++ and honggfuzz; coverage-guided native fuzzing | both upstream; **L estimated**; Clang/LLVM/compiler runtime; Apache-2.0 / Apache-2.0 | Each engine crashes and minimizes the same deliberately vulnerable hashed parser under PID/CPU/disk caps. |
| **Must, gated use** · Web | feroxbuster, dalfox, nuclei, SSTImap, jwt_tool, Arjun, smuggler/h2csmuggler, Katana/httpx/interactsh, Playwright+Chromium, SecLists/PayloadsAllTheThings, phpggc/ysoserial | both or architecture-neutral; **XL estimated**; Go/Python/Java/Node/browser; exact upstream notices pinned per artefact | A local vulnerable service proves one distinct result per tool; active traffic is Target-only, capped and recorded. Payload generators execute only in the fixture. |
| **Must** · Forensics | Zeek, Volatility3+dwarf2json+symbols, tcpflow, binwalk, PCredz, testdisk, bulk_extractor, oletools, peepdf-3, qpdf, Scapy | both/source where packaged; **XL estimated**; Python/native parsers and symbol corpus; BSD/GPL/Volatility notices by project | Each tool recovers its own known flow/file/process/document indicator from a hashed fixture; PCredz output is secret-bearing. |
| **Redundant** · Forensics | foremost and tcpdump | both; **~24 MiB plus 0.2 MiB** metadata estimate; libc/libpcap; GPL-2+/BSD | Existing Scalpel and TShark cover equal/deeper paths. Comparative fixtures must show no unique recovery before omission. |
| **Must** · Stego, Misc | stegseek, stego-lsb/stegoveritas, zxing-cpp+zbar, PySSTV/sstv | both/source; **M estimated**; image/audio libraries; GPL/MIT/BSD notices by project | Each decoder reads its own embedded-text, QR or SSTV fixture. ZXing and zbar stay because their paths differ. |
| **Useful, detected RF path** · Hardware/RF | sigrok-cli, multimon-ng, minimodem, rtl_433 | both/source; **M estimated**; audio/DSP libraries; GPL notices by project | Each decoder reads its own logic, AFSK or ISM capture. No physical-device authority. |
| **Useful, detected-chain path** · Blockchain | Foundry, heimdall-rs, Medusa, Halmos, web3.py/eth ABI/account, crytic-compile, evmole, pinned solc | arm64+amd64 except release-specific artefacts; **L estimated**; Rust/Go/Python/Z3/EVM; Apache/MIT/GPL notices by project | Each tool deploys, inspects, decompiles, fuzzes, symbolically tests or exploits its part of a local contract. No public-chain funds/API. |
| **Useful, chain-triggered** · Blockchain | ityfuzz; Solana/Move toolchain | both where source builds; large LLVM/Rust closure, cost **unmeasured**; project-specific SDK; OSS licences pinned at build | Prebuilt deterministic profile after chain detection. Exploit a local source-less EVM or Solana fixture; no mainnet endpoint. |
| **Must** · OSINT | Maigret, Sherlock, holehe, theHarvester plus geopy/folium/piexif/staticmap | Python/all; **M estimated**; live HTTP/geospatial libraries; GPL/MIT notices by project | Each client/parser uses recorded responses or one controlled identity under rate/captcha caps; no inherited accounts. Coverage differs by username/email/geospatial input. |
| **Must** · Misc, jail | pyjailbreaker, Qiling, local CyberChef library wrapper, webcrack | both/all; **M estimated**; Python/Unicorn/Node; MIT/GPL/Apache notices by project | Each escapes a disposable jail, emulates a foreign binary, decodes or deobfuscates its known fixture. Wrapper avoids the 463-tool MCP surface. |
| **Useful, format-triggered** · Game, Rev | .NET runtime/SDK+ilspycmd, Cpp2IL, Il2CppInspectorRedux/Il2CppDumper, AssetRipper/UABEA/AssetsTools.NET, GDRE, CUE4Parse/UnrealExporter, WABT/webcrack | native both where built; old x86-64 releases are not portable; **XL estimated**; .NET/Java/Node; exact OSS notices per artefact | Every admitted extractor gets a real Unity/Godot/Unreal/.NET/WebAssembly fixture and asserts a known method/asset. |
| **Useful, gated** · Game, Mobile | Frida client/server, frida-il2cpp-bridge, scanmem | client both; server/scanmem target-specific; cost **unmeasured**; device/process/ptrace surface; wxWindows/GPL/MIT mix | Disposable dynamic-target sandbox. Hook one fixture method or find one process value, then prove egress isolation and teardown. |
| **Useful, provider-triggered** · Cloud | official AWS CLI/SDK, Google Cloud CLI/libraries and Azure CLI/SDK | architecture-neutral Python/native helpers; **XL estimated**; Python/Java helpers; Apache-2.0/MIT plus service terms | Each admitted CLI parses its local IAM/config artefact; authenticated probes use disposable emulators or Challenge credentials and prove credential deletion. |
| **Must compact baseline** · AI/ML forensics | fickling, picklescan, modelscan | both/all where wheels exist; **M estimated**; Python/model parsers; BSD/Apache notices by project | Each scanner detects its known malicious model fixture without loading it. |
| **Useful, task-triggered** · AI/ML | ART, torchattacks, garak, pyrit | both/all where wheels exist; **XL estimated** with PyTorch; Python/ML frameworks; MIT/Apache notices by project | Adversarial libraries attack a tiny checked-in model; prompt tools test only a local mock or Challenge-supplied model endpoint. No local inference model is bundled. |
| **Unsupported/excluded** · viewers | Wireshark GUI, Autopsy, NetworkMiner, FModel, PINCE, UModel, dnSpy/Cheat Engine, AssetStudio, angr-management, IDA | GUI/platform/proprietary constraints; costs vary; display/session runtimes | No operable unattended display/licence path. TShark, headless extractors, ilspycmd, scanmem, angr and Ghidra supply the stronger autonomous paths. |
| **Unsupported/excluded** · hype/dead | Python Ciphey, KLEE/SymCC without source, PaddleOCR arm64, NoSQLMap, local LLM/decompiler checkpoints, VibeHacking, pentest-ai/pentestMCP, mctfp/ctf-mcp | dead/incompatible or multi-GiB unproved closures; licences vary | Excluded for the item-specific maintenance, architecture, source-input, scope or authority reasons below. Probe the maintained replacement path; import success is not capability. |
| **Redundant/unsafe integration** · MCP | duplicate proxy/Frida MCPs, CyberChef-MCP, paid Etherscan MCP, broad Ghidra/Frida/CTF MCPs | Node/Python; costs/tool surfaces vary; licences sometimes unclear | Direct CLI/library is equally capable with a smaller authority/context surface. Any future MCP needs source, egress and tool-surface audit plus an identical fixture comparison. |

Distinct bundled purposes are: `dwarf2json` builds Volatility Linux symbols; PCredz extracts
credentials from supplied captures; `phpggc` and `ysoserial` generate PHP and Java gadget payloads;
Interactsh observes out-of-band callbacks; Cpp2IL rebuilds IL from IL2CPP metadata while
Il2CppInspector maps types and Il2CppDumper is the fast fallback; ART supplies framework-wide
adversarial methods, torchattacks the PyTorch-specific path, garak probes model-interface
weaknesses, and pyrit orchestrates prompt red-team cases. These are candidate differentiators,
not current install claims.

The legacy workspace's grouped exclusions resolve per item as follows; current status remains
subject to #186's primary-source check. FModel and PINCE are GUI-bound (replace with
CUE4Parse/UnrealExporter and scanmem); UModel is closed Windows software (CUE4Parse); dnSpy and
Cheat Engine are Windows GUIs (ilspycmd and scanmem); AssetStudio is archived (AssetRipper/UABEA);
angr-management is a GUI over already-selected angr; IDA is proprietary (Ghidra headless). Python
Ciphey is unmaintained (maintained Rust decoder/CyberChef path); KLEE and SymCC require source and a
special build (AFL++/angr cover binary-first work); PaddleOCR lacks the required arm64 path
(Tesseract); NoSQLMap is Python-2-era (bounded hand-written probes). LLM4Decompile, manga-OCR and
Aletheia require local model checkpoints and are excluded by scope. CyberChef-MCP exposes 463
context-heavy tools (thin wrapper); the surveyed Etherscan MCP requires an external public-chain
API account (Foundry/web3);
broad Ghidra/Frida/CTF MCPs add shell, device or scoreboard authority without capability beyond the
selected CLI/library. Autopsy is a desktop GUI over Sleuth Kit (use CLI Sleuth Kit/Volatility);
NetworkMiner is a desktop packet-forensics GUI (TShark/Zeek). The surveyed mega-MCPs were
VibeHacking, pentest-ai/pentestMCP and mctfp/ctf-mcp; their infrastructure/scoreboard authority is
outside this tool inventory. Unclear-licence MCPs fail closed.

The old trust split—native trusted computation, isolated Challenge-owned execution—survives as a
security requirement. Its host-vs-amd64 layout does not: current architecture and packaging must
be proved in the present Solver image. The old `sandbox/INSTALLED.md` also records concrete
warnings worth carrying forward: several fetched game artefacts were x86-64, EasyOCR lacked
weights, a Playwright wrapper named a nonexistent CLI, and best-effort installs previously allowed
green images with absent tools.

## Why this is the closed expected surface

“Complete” means every recurring classic or plausible open-string input/interaction shape has a
low-friction primitive **and a complementary deep path where it changes solve odds**:

- bytes, archives, filesystems, images and PDFs already had a floor; 7-Zip, OCR, media streams,
  SQLite, steghide and PCAP fill observed format holes;
- native, managed, WebAssembly and game executables gain decompilation, symbolic analysis,
  debugging, fuzzing, patching and foreign execution;
- APK/DEX gains complementary static views and an authority-gated dynamic path;
- the proposed surface provides request/response, discovery and offline protocol work; authority and rate limits,
  not absence, prevent misuse;
- Crypto and programming gain CAS, lattice, SMT/SAT/CP and cracking paths;
- hardware/RF, blockchain, cloud, OSINT, jail and unknown strings each map to a queryable profile.

The suite stays adaptive in selection. Modern agent evidence says a broad,
queryable environment improves aggregate solving, while tool names alone create no capability.
Every shipped row therefore needs an executable real-input probe and an inventory record. A future
Run may challenge a redundant item only with an equal-path comparison; size alone is insufficient.

## Implementation handoff

[Issue #186](https://github.com/jerome-queck/incypher-ctf/issues/186) converts this capability
classification into exact v2 packages, layers, locks, probes and the truthful agent-visible
manifest. This research intentionally does not freeze those production choices. Implementation
should split by profile and boundary:

1. lock every package/source artefact, architecture, licence and digest; generate the installed
   inventory from what the image actually holds;
2. measure each profile and the **combined** arm64/amd64 image, including build time and size;
3. keep parser probes in the build where fixtures are cheap and licence-compatible;
4. put deterministic execution/network probes in a no-network or loopback-only script run by CI
   and the pinned runtime; put OSINT, cloud and model-endpoint smokes in a separately authorized
   live Gate with disposable credentials and durable rate/request caps;
5. require a functional assertion for **every admitted tool/capability** (fixtures may be shared),
   including foreign ELF, APK, PCAP/memory/disk, web
   target, contract, RF/audio, game asset, cloud config and jail fixtures;
6. layer large profiles if needed for build/transfer reliability, but prebuild and attach them
   deterministically where measurement supports it; ADR-0024's explicit run-time `apt`/`pip`
   long tail remains authoritative until a later ADR changes it.

## Newly surfaced Wayfinder decisions

Two genuinely separate security decisions remain; cost measurement is implementation evidence,
not authority to shrink the suite:

- **The dynamic-target execution boundary** — will Mobile/Windows/full-system dynamic analysis be
  enabled for a given Challenge, and what disposable sandbox owns emulator images, privileges,
  egress, credentials and teardown?
- **The adversarial-parser boundary** — before adding the larger parser surface, is the current
  outer container alone sufficient, or must parsers and executed Challenge code move behind a
  separate uid/seccomp/resource boundary?

Packaging/profile implementation must also resolve three pieces of fog without reducing scope:
Nmap is absent from the captured Kali indexes despite its tracker, several legacy game releases
are x86-64-only, and cloud CLI combined cost is unmeasured. Each needs a pinned supported source or
an equal-capability replacement plus a real-input probe.

The 14 September ADK/first official batch may reveal genuinely new Categories or target formats;
only those evidence-backed deltas belong to v3. All capabilities and extension seams knowable from
current organiser statements, Runs, corpus, modern writeups and upstream packaging remain v2.

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
- Machine-local legacy evidence was read at `ctf-workspace` commit `4631053`: `docs/tooling.md`,
  ground-truth `sandbox/INSTALLED.md`, `sandbox/Dockerfile`, `docs/archive/tooling-p2.5.md`,
  `docs/adr/0021-tooling-split.md`, install scripts, smoke scripts and `docs/ISSUES.md`. This is
  evidence of candidates and past install failures, not a portable dependency or current design.
- Brunner's audited command/runtime surface comes from machine-local
  `docs/solver-tools.md` at commit `92e3399`. Its absence of system-install authority is specific
  to that harness; its isolated dependency/cache discipline transfers.

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
- [SageMath](https://github.com/sagemath/sage), [fpylll](https://github.com/fplll/fpylll),
  [Z3](https://github.com/Z3Prover/z3), [cvc5](https://github.com/cvc5/cvc5),
  [OR-Tools](https://github.com/google/or-tools), [PySAT](https://github.com/pysathq/pysat), and
  [MiniZinc](https://github.com/MiniZinc/libminizinc) establish the solver capabilities and
  licences; final packaging still needs pinned closure measurement.
- [AFL++](https://github.com/AFLplusplus/AFLplusplus),
  [honggfuzz](https://github.com/google/honggfuzz),
  [Volatility 3](https://github.com/volatilityfoundation/volatility3),
  [Zeek](https://github.com/zeek/zeek), and [YARA](https://github.com/VirusTotal/yara) are the
  primary fuzzing/forensics sources.
- [Foundry](https://github.com/foundry-rs/foundry),
  [Medusa](https://github.com/crytic/medusa), [Halmos](https://github.com/a16z/halmos),
  [Heimdall](https://github.com/Jon-Becker/heimdall-rs), and
  [web3.py](https://github.com/ethereum/web3.py) establish the self-hosted EVM surface.
- [Frida](https://github.com/frida/frida), [Qiling](https://github.com/qilingframework/qiling),
  [GoReSym](https://github.com/mandiant/GoReSym),
  [ILSpy](https://github.com/icsharpcode/ILSpy), [Cpp2IL](https://github.com/SamboyCoding/Cpp2IL),
  [AssetRipper](https://github.com/AssetRipper/AssetRipper), and
  [GDRE Tools](https://github.com/GDRETools/gdsdecomp) establish the dynamic/game paths and their
  platform constraints.
- Current verification starting points for legacy exclusions are
  [AssetStudio](https://github.com/Perfare/AssetStudio),
  [NoSQLMap](https://github.com/codingo/NoSQLMap),
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), and
  [CyberChef-MCP](https://github.com/doublegate/CyberChef-MCP). The crosswalk deliberately marks
  unverified legacy claims rather than inferring current support from them.
- Official [AWS CLI](https://github.com/aws/aws-cli),
  [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), and
  [Azure CLI](https://github.com/Azure/azure-cli) sources establish supported unattended clients;
  service authorization is intentionally separate from package availability.

### Representative modern writeups (demand only)

- [NahamCon CTF 2024 Mobile](https://blog.ikuamike.io/posts/2024/nahamcon_ctf_2024_mobile/)
  uses JADX, an Android emulator and ADB/logcat.
- [Google CTF 2025, The Classic Notes App](https://utkar5hm.github.io/posts/googlectf25-the-classic-notes-app/)
  uses QEMU AArch64 and GDB across an architecture boundary.
- [BDSec CTF 2025, revME](https://kshackzone.com/ctfs/writeups/NomanProdhan/70/bdsec-ctf-2025/reverse-engineering/revme)
  is the challenge author's radare2-based solution.

## Uncertainties that remain

- Combined image delta and build time are metadata estimates until Colima is healthy and both
  architecture builds run. Cost may justify deterministic layers, not capability removal.
- The official 2026 Category names and file/protocol distribution are not yet released. This is a
  coverage decision over the known expected Categories, old corpus and current Runs, not a claim
  about unseen Challenges.
- Nmap's tracker/index disagreement may be transient. Implementation needs a pinned supported
  source or equal bounded scanner; supplied-host discovery cannot silently disappear.
- Legacy source installs, cloud CLIs, solver stacks, game releases and symbol corpora lack one
  comparable current installed-size measurement. Their rows say unmeasured or explicitly estimated.
- The correct boundary for dynamic Android and Windows targets is a separate execution-sandbox/emulator
  design. Adding packages to this container cannot settle it.
- Parser exposure should be threat-modelled before landing the layer: JADX, Apktool, FFmpeg,
  TShark, archive and image parsers all consume adversarial bytes inside the outermost current
  boundary.
