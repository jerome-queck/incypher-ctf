# One immutable image exposes only proved free Tool components

> **ADR-0057 moved the VM onto the external Working volume. ADR-0058 now makes host image/cache
> sizing and the 4 GiB profile-delta value advisory; signed Tool receipts and per-worker Resource
> envelopes remain authoritative. Issue #302's final fielding narrows OSINT to the brokered
> adapters actually proved and retains SMB as triggered/unavailable.**

[The complete v2 tool image](https://github.com/jerome-queck/incypher-ctf/issues/186)
turns the capability coverage from
[the complete per-Category tool inventory](https://github.com/jerome-queck/incypher-ctf/issues/183)
into a shippable contract. **v2 contains one immutable multi-architecture image with a small resident
floor and deduplicated prebuilt Tool profiles; it advertises only free, pinned, real-input-proved and
headlessly operable Tool components, while typed Capability handles separately control what one
Engagement may use.** An aspirational catalogue, package on `PATH` or successful import is never a
capability claim.

This record keeps [ADR-0024](0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)'s
measured floor and picture attachment, but supersedes its global run-time `apt`/`pip` long tail for
v2 Gate and scored Runs. It composes [ADR-0041](0041-one-container-separates-control-from-hostile-execution.md)'s
authority boundary, [ADR-0042](0042-one-controller-owns-four-agent-roles-and-every-engagement.md)'s
Engagement contract and [ADR-0045](0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)'s
sealed evidence.

## Capability and component are different decisions

A **Tool capability** is a distinct operation useful for Challenge work. A **Tool component** is one
installed binary, library, runtime, corpus or sysroot which supplies one or more capabilities. This
separation stops a long candidate list from becoming an entitlement and stops rejecting one unsafe
package from silently deleting the capability it was meant to provide.

Every capability has one reviewed status:

- `required`: representative demand, a unique or measured advantage over retained primitives and a
  bounded real-input probe establish it;
- `triggered`: a detected format, protocol, hypothesis or open Category makes it useful, but it is
  not part of the ordinary surface;
- `deferred`: present evidence cannot yet establish the need or a viable component; or
- `excluded`: evidence places the capability beyond this Solver's autonomous container boundary.

Every admitted component independently has one placement:

- `resident`: part of the small cross-cutting floor exposed to every Attempt executor;
- `profile-contained`: immutable in the image and exposed only with a selected Tool profile; or
- `rejected`: the component failed the licence, provenance, architecture, functional or autonomous
  orchestration bar.

Authority is orthogonal to placement: a resident or profile-contained component may still require a
current identity-bound Capability handle. `unavailable` is a Tool view state for a capability which
has no admitted component, not a fictional component status.

Plausibility alone earns `triggered`, not `required`. Cost may move a component out of the resident
floor, reject a duplicate after an equal-path comparison, or keep a speculative capability
unavailable. It may not erase a unique required capability without recording the evidence and the
gap. Category remains an open Board string: it may seed profile selection but never limits which
profiles a Challenge can compose.

## The admission bar is deliberately strict

A component ships only when all of these are true:

1. its authoritative licence permits use and redistribution in the private-source Solver image and
   a possible organiser handover; non-free, proprietary, account-bound, unlicensed or materially
   ambiguous components are rejected;
2. its exact source, version, bytes, dependency closure, licence notices and supported architecture
   are locked;
3. it performs its distinct capability on real input inside the exact container isolation and
   Resource envelope where an Agent will use it; and
4. an Agent can operate it autonomously and headlessly without a desktop session, ambient account,
   public callback service, host authority or an unbounded external dependency.

Failure selects a free equal-capability replacement where one exists. Otherwise the Tool view says
`unavailable` with the evidence pointer; it never invents equivalence or hides the gap. A component
which cannot be proved to work is rejected, not installed optimistically.

Consequently v2 rejects Nmap's non-free NPSL component and exposes only a bounded controller-owned
TCP-connect, banner and TLS partial fallback while truthfully leaving service-fingerprint and NSE
depth unavailable. It ships free `unrar-free`, replacing it with `unar` only if the RAR4, RAR5,
encrypted, solid and multivolume fixtures fail; non-free RAR codecs stay out. Public Interactsh/OAST
services stay out because they leak Challenge-derived callbacks and add an uncontrolled run-time
dependency; only a Challenge-supplied or explicitly approved controlled endpoint can receive that
Capability handle.

Honggfuzz is the native-fuzzing component. AFL++ remains triggered unless a matched fixture proves
a material solve or time advantage and its changed licence obligations pass the same admission bar.
Apktool is the pinned upstream 3.0.3 Java component rather than Kali's stale 2.7.0 package. PickleScan
and Fickling form the resident model-file baseline; ModelScan may enter only a locked CPython 3.12
profile after a non-pickle fixture proves distinct coverage. StegoVeritas and unlicensed
`pyjailbreaker` are rejected. GoReSym is source-built and proved separately for arm64 and amd64;
generic amd64-only release assets never masquerade as multi-architecture. `pwninit` and `ipsw`
remain triggered until distinct evidence promotes pinned per-architecture source builds.

Package-name identity is verified, not inferred: Kali's `foundry`, `medusa` and
`python3-keystone` are not the intended blockchain Foundry, Crytic Medusa or Keystone assembler.
Those capabilities use pinned upstream components or the correctly identified package. The image
manifest also states that the free `7zip` build has no RAR codec rather than inheriting a claim from
a differently licensed build.

## One image contains a resident floor and immutable profiles

The official material currently requires only “a Docker container”; it states no host architecture,
multi-architecture index or delivery format. v2 therefore treats arm64 and amd64 parity as an
internal handover hedge, not organiser-stated compliance. The resident required surface passes on
both. An architecture-specific triggered component declares its binding and remains unavailable on
an unproved platform. The official ADK and demo may change the scored artifact binding only through
the later v3 official-delta decision.

The resident floor holds cheap, broadly useful and authority-light primitives:

- shell, file and recon utilities: `file`, `binutils`, `binwalk3`, `xxd`, `jq`, `ripgrep` and
  `git`;
- `python3`, `python3-requests`, `curl`, `wget`, `openssl`, `netcat-openbsd` and `sqlite3`;
- `unzip`, `7zip`, `unrar-free`, `bzip2`, `xz-utils`, `zstd`, `cpio` and `squashfs-tools`;
- `libimage-exiftool-perl`, `imagemagick-7.q16`, its proved font dependencies, `python3-pil`,
  `poppler-utils`, `python3-pypdf`, `tesseract-ocr` and `zxing-cpp-tools`;
- `python3-pycryptodome`, `python3-sympy`, `python3-gmpy2` and `python3-z3`; and
- controller-only resource primitives named below, never exposed as Agent lifecycle authority.

ADR-0024's global PEP-668 marker removal is removed with its run-time install path. `python3-pip`
may exist only where an attempt-local locked environment needs it; it is not resident authority to
mutate the image. `bubblewrap` likewise remains only if the actual v2 isolation probe makes it an
operable component; its historical presence does not pass this record's admission bar.

Immutable logical Tool profiles cover `crypto`, `web`, `pwn`, `foreign-elf`, `reverse`,
`forensics`, `disk-adapters`, `stego-media`, `osint`, `ai-ml`, `misc-protocols` and
`mobile-static`, plus detected `programming`, `game`, `blockchain`, `cloud`, `hardware-rf` and
`boot-root` extensions. Components are stored once and referenced by digest across profiles: Java,
LLVM/build tools, foreign loaders and sysroots, Z3, media recognition, PCAP tooling, modern .NET,
Chromium, symbols, wordlists and fixtures are not copied per Category.

The required anchors are the minimum non-overlapping functional surface, not a demand to install
every name in the research catalogue:

- Crypto adds `sagemath`, `python3-fpylll` and `john` to the resident PyCryptodome,
  SymPy/gmpy2/Z3 floor. A maintained Coppersmith/factoring source and alternate cvc5, OR-Tools,
  PySAT or MiniZinc engines remain triggered until a distinct fixture or detected input language
  selects them.
- Web starts with `ffuf`, Playwright/Chromium and curated SecLists/PayloadsAllTheThings data.
  Arjun, `jwt_tool`, SSTImap, `sqlmap`, Dalfox, Nuclei, request-smuggling clients, `phpggc` and
  `ysoserial` are hypothesis-triggered and Target-rate-bounded rather than blanket reconnaissance;
  Gobuster and Feroxbuster do not duplicate ffuf without a differential result.
- Pwn composes `build-essential`, GCC/G++, CMake, pkg-config, `python3-pwntools`,
  `gdb-multiarch`, `patchelf`, `strace`, `ltrace`, `seccomp-tools` and honggfuzz. ROPgadget already
  arrives through pwntools; Ropper, angrop, GEF and pwninit do not enter merely as overlapping
  convenience. `foreign-elf` adds `qemu-user`,
  pinned opposite-architecture loaders/sysroots and an end-to-end QEMU remote-GDB probe; a static
  hello alone cannot establish dynamic foreign execution.
- Reverse composes `radare2`, headless Ghidra, WABT, `upx-ucl`, Capstone, Unicorn,
  `python3-keystone-engine`, angr, source-built GoReSym and modern .NET plus `ilspycmd`. Mono is a
  triggered legacy-execution path.
- Forensics composes `tshark`, Scapy, YARA, Volatility 3, `dwarf2json`, locked symbols, `oletools`
  and `qpdf`, plus only distinct proved flow, memory, document and carving paths. `disk-adapters`
  uses `ewf-tools`, `afflib-tools`, `qemu-utils`, `libbde-utils`, `libfsapfs-utils`,
  `libvshadow-utils` and `dislocker` read-only for E01, AFF, VM-disk, BitLocker, APFS and VSS;
  raw-image Sleuth Kit does not replace container adapters.
- Stego/media composes `ffmpeg`, zsteg, `steghide`, `stegseek`, pinned `stego-lsb` and pinned
  PySSTV. Overlapping barcode, LSB and SSTV decoders remain comparative until one recovers a format
  the selected components miss.
- OSINT composes Research-broker DNS, pinned Sherlock, Holehe, theHarvester plus the broker's RDAP
  parser, and geopy coordinate normalisation. Standalone `bind9-dnsutils`, `whois`, Piexif and
  StaticMap have no proved production-handle result and do not ship. No inherited account enters
  the image.
- Mobile static composes JADX, pinned Apktool 3.0.3, `libplist-utils`, IPA/archive handling and
  shared Mach-O, Objective-C/Swift, SQLite and decompiler paths. `android-sdk-platform-tools`, an
  emulator, Frida, device and `ipsw` work remains triggered and authority-gated.
- AI/ML begins with PickleScan and Fickling. ModelScan's separate CPython 3.12 component and heavy
  ART, Torchattacks, Garak or PyRIT paths are task-triggered; no local inference model ships.
- Misc/protocols composes a pinned CyberChef release through a Solver-owned thin CLI, Qiling,
  `webcrack`, `socat` and curated jail guidance. SMB remains triggered until its own semantics are
  proved; generic TCP relay is not that proof. `sigrok-cli`, `multimon-ng`,
  `minimodem` and `rtl_433`; upstream blockchain/chain SDKs; provider CLIs; engine-specific game
  extractors; and Go/Rust toolchains attach only on detected evidence and after their own admission
  probes.

An unknown Category starts with the resident floor and `misc-protocols`; input evidence may select
any other profile. “Attach” means deterministic exposure of components already in the image at an
Engagement or Turn boundary. It never means downloading, installing or starting a second image.
Active Web, public research, hostile execution, ptrace/fuzzing, device/emulator/full-system/Wine,
cloud/chain credentials and live interfaces remain independently gated by ADR-0041's typed
authority.

## The component and local-Board matrix is normative

The tables below, not the earlier research catalogue, are the v2 component decision. Every
`required` row must be resident or profile-contained in the release image and each capability id
must have its own semantic assertion in a local-Board Challenge receipt. One Challenge may cover
several ids, but one family-level smoke cannot stand in for them. Any component not named below does
not ship without a reviewed change to this record and the manifests.

| Required capability ids | Placement and selected Tool components | Required local-Board input and semantic result |
| --- | --- | --- |
| `recon.mime`, `recon.bytes`, `recon.repository` | resident: `file`, `binutils`, `binwalk3`, `xxd`, `jq`, `ripgrep`, `git` | Lying extensions, embedded bytes and a leaked Git history; identify content and recover the hidden revision value. |
| `archive.extract`, `firmware.rootfs` | resident: `unzip`, `7zip`, `unrar-free`, `bzip2`, `xz-utils`, `zstd`, `cpio`, `squashfs-tools` | ZIP, 7z, RAR4/5 encrypted/solid/multivolume, cpio and SquashFS fixtures; recover bounded payloads without traversal or expansion escape. |
| `network.http`, `network.tcp`, `data.sqlite` | resident: `curl`, `wget`, `openssl`, `python3-requests`, `netcat-openbsd`, `sqlite3` | Local HTTP/TLS/TCP Targets and an immutable SQLite file; round-trip the protocol facts and query the held value. |
| `image.inspect`, `document.pdf` | resident: `libimage-exiftool-perl`, `imagemagick-7.q16`, `gsfonts`, `python3-pil`, `poppler-utils`, `python3-pypdf` | Metadata-bearing image, tiled/annotated image, text and image-only PDFs; recover dimensions, metadata and text while preserving inputs. |
| `recognition.ocr`, `recognition.barcode` | resident: `tesseract-ocr`, `zxing-cpp-tools` | Noisy text raster plus QR and one non-QR barcode; decode the known values within pixel and time bounds. |
| `crypto.primitive`, `math.symbolic`, `solver.smt` | resident: `python3-pycryptodome`, `python3-sympy`, `python3-gmpy2`, `python3-z3` | AES/RSA, CRT/modular and bit-vector constraints; recover and verify each known answer. |
| `crypto.cas`, `crypto.lattice`, `crypto.password` | `crypto`: `sagemath`, `python3-fpylll`, `john` | CAS, small lattice and bounded hash/archive-password Challenges; recover the answer and record CPU/RAM/time. |
| `web.discovery`, `web.browser` | `web`: `ffuf`, pinned Playwright/Chromium, pinned SecLists and PayloadsAllTheThings subsets | Local vulnerable origin with hidden route and browser-only state; discover each under request/rate bounds with no off-origin traffic. |
| `pwn.build`, `pwn.elf`, `pwn.debug`, `pwn.trace`, `pwn.seccomp` | `pwn`: `build-essential`, GCC/G++, CMake, pkg-config, `python3-pwntools`, `gdb-multiarch`, `patchelf`, `strace`, `ltrace`, `seccomp-tools` | Compile a fixture, inspect ELF/ROP/interpreter, debug a native crash, trace syscall/library facts and decode a seccomp policy. |
| `pwn.fuzz` | authority-gated `pwn`: honggfuzz | Crash and minimise one deliberately vulnerable parser under PID/CPU/disk/output ceilings. |
| `foreign.dynamic`, `foreign.debug` | authority-gated `foreign-elf`: `qemu-user`, Debian-snapshot `libc6-arm64-cross` and `libc6-amd64-cross`, shared `gdb-multiarch` | On each host architecture run a dynamically linked opposite-architecture ELF against its packaged loader/sysroot and attach remote GDB; a static hello does not pass. |
| `reverse.disassemble`, `reverse.decompile` | `reverse`: `radare2`, headless Ghidra | Recover functions and one decompiled decision from native and foreign hashed ELFs without executing them. |
| `reverse.wasm`, `reverse.unpack`, `reverse.emulate`, `reverse.symbolic` | `reverse`: WABT, `upx-ucl`, Capstone, Unicorn, `python3-keystone-engine`, angr | Validate/decompile Wasm, unpack a copied ELF, assemble/disassemble/emulate instructions and solve a branching binary. |
| `reverse.go`, `reverse.dotnet` | `reverse`: pinned per-architecture GoReSym source build, Microsoft-repository `dotnet-runtime-8.0` and pinned `ilspycmd` | Recover stripped Go symbols and a managed method from source-built hashed fixtures on each declared platform. |
| `forensics.pcap`, `forensics.signature` | `forensics`: `tshark`, `python3-scapy`, YARA | Recover fields and an uncommon layer from PCAP/PCAPNG and match a generated signature; live capture remains denied. |
| `forensics.memory` | `forensics`: pinned Volatility 3, `dwarf2json`, and a Linux ISF generated from a pinned kernel.org stable Linux source commit plus repository-owned build config | Enumerate the known process/module from the matching repository-generated Linux memory fixture under parser bounds. |
| `forensics.document`, `forensics.filesystem`, `forensics.carve` | `forensics`: `oletools`, `qpdf`, Sleuth Kit, Scalpel | Recover the distinct macro/PDF, deleted filesystem and carved-file facts from hashed images. |
| `forensics.disk-container` | `disk-adapters`: `ewf-tools`, `afflib-tools`, `qemu-utils`, `libbde-utils`, `libfsapfs-utils`, `libvshadow-utils`, `dislocker` | Open E01, AFF, QCOW/VMDK, BitLocker, APFS and VSS fixtures read-only; expose the inner filesystem while every held-input hash stays fixed. |
| `media.inspect`, `stego.image`, `stego.audio`, `stego.sstv` | `stego-media`: `ffmpeg`, zsteg, `steghide`, Stegseek, pinned `stego-lsb`, pinned PySSTV | Extract a stream/frame and recover distinct PNG/BMP, JPEG/WAV and SSTV payloads under byte/time bounds. |
| `osint.dns`, `osint.identity`, `osint.email`, `osint.domain`, `osint.geo` | authority-gated `osint`: Research-broker DNS, pinned Sherlock, Holehe, theHarvester plus broker RDAP, and geopy | Recorded responses plus controlled live identities/domains/coordinates; recover each distinct fact within provenance and rate policy and without inherited accounts. |
| `mobile.android-static`, `mobile.ios-static` | `mobile-static`: JADX, pinned Apktool 3.0.3, `libplist-utils`, shared ZIP/SQLite/radare2/Ghidra | Decode/rebuild a modern APK and recover manifest/class data; inspect IPA, binary/XML plist, Mach-O metadata and an iOS backup database. |
| `model.pickle-inspect` | `ai-ml`: pinned PickleScan and Fickling | Detect and explain distinct malicious pickle opcodes without loading the model. |
| `misc.transform`, `misc.emulate`, `protocol.relay`, `jail.reason` | `misc-protocols`: pinned CyberChef release with Solver-owned thin CLI, Qiling, `webcrack`, `nodejs`, `socat`, shared resident `python3` and `dash` at `/bin/dash`, and a versioned jail playbook | Decode/deobfuscate known transforms, emulate a foreign fixture, relay admitted streams and solve disposable JavaScript, Python and Dash jails. Other language runtimes use the triggered `language.extra` path. |

Triggered capabilities below begin unavailable. A named candidate becomes profile-contained only
after the full admission bar and its row's differential or detected-input probe passes; otherwise
the manifest retains the capability and reason but contains no bytes. Authority-gated rows also need
their current handle. This conditional is the exact component decision, not a best-effort installer.

| Triggered capability | Candidate components and promotion proof |
| --- | --- |
| `solver.alternate`, `crypto.coppersmith` | cvc5, OR-Tools, `python-sat`, MiniZinc and a maintained Coppersmith/factoring source only for a detected formulation or a fixture the required anchors cannot solve within bounds. |
| `web.hypothesis-tools` | Arjun, `jwt_tool`, SSTImap, `sqlmap`, Dalfox, Nuclei without OAST, request-smuggling clients, `phpggc` and `ysoserial` only on a distinct local vulnerable Target result. Gobuster/Feroxbuster remain rejected while ffuf is equal. |
| `pwn.alternate-fuzzer`, `pwn.convenience` | AFL++ only after a matched honggfuzz win and licence review; Ropper, angrop, GEF and source-built pwninit only after a distinct result or material Step/time gain. |
| `reverse.legacy-dotnet`, `reverse.ipsw` | Mono only for a legacy CLR execution fixture; per-architecture source-built `ipsw` only for a distinct IPSW/dyld fixture. |
| `forensics.flow-analysis` | Zeek, tcpflow, testdisk, bulk extractors or extra PDF/carving tools only where the required TShark/Sleuth Kit/Scalpel/document paths miss a named fact. |
| `recognition.alternate`, `stego.alternate` | zbar or another barcode, LSB or SSTV decoder only on a format missed by ZXing, `stego-lsb` or PySSTV. StegoVeritas remains rejected. |
| `model.multi-format`, `model.adversarial` | ModelScan in locked CPython 3.12 only for distinct H5/Keras/SavedModel coverage; ART, Torchattacks, Garak or PyRIT only for a detected model-endpoint task. |
| `mobile.dynamic`, `system.emulation`, `windows.execute` | Android emulator/Frida, `qemu-system-*`, Wine and Scanmem only after disposable guest, egress, credential, rollback and teardown proofs. |
| `hardware-rf.decode` | `sigrok-cli`, `multimon-ng`, `minimodem`, `rtl_433` only for detected capture formats; no physical-device authority. |
| `blockchain.toolchain`, `cloud.provider`, `game.extract`, `language.extra` | Correctly identified pinned upstream chain tools/SDKs, provider CLIs, engine-specific extractors and Go/Rust toolchains only after a representative detected-input fixture on each declared platform. |
| `network.service-fingerprint` | No component: Nmap is rejected as non-free; the resident bounded TCP/banner/TLS probe is explicitly partial. |
| `protocol.smb` | No component: generic TCP relay is not SMB semantics. Promote only after a licensed client and bounded local SMB Target pass on both platforms. |
| `web.oast`, `jail.pyjailbreaker` | No component: public OAST and unlicensed `pyjailbreaker` are rejected. A Challenge-supplied controlled callback or newly licensed component requires a reviewed manifest change. |

The resident and required-profile rows are the minimum release closure. All legacy catalogue names
not present in either table are rejected as unproved or overlapping rather than carried forward by
silence.

## Required capabilities never depend on a package registry during a Run

The image is rebuilt from a digest-pinned multi-architecture base, an immutable signed package-index
snapshot and exact per-platform package closures. Upstream binaries or source builds carry immutable
release or commit identities and verified bytes; the build emits source and installed digests,
dependency locks, SBOM and licence/notice inventory. Any unresolved checksum, licence or required
architecture fails the affected profile build. Installers have no best-effort path.

Gate and scored Runs disable external `apt`, pip, gem, npm, Cargo and similar registry installation.
An attempt-local, unprivileged install may consume only a bounded content-addressed cache already in
the image, and a component stays unavailable until that install and its functional probe succeed.
Network installation is permitted only in explicitly labelled rehearsal and never becomes Gate
evidence. If the official rules later permit and make run-time network installation strategically
useful, v3 may add that official-delta behavior.

No dependency refresh is automatic. A reviewed lock change creates a new candidate image and reruns
every affected probe. A security or official-input change invalidates the candidate rather than
mutating it. A Gate candidate is named by its immutable image digest.

## Three records tell the truth at different resolutions

The build generates a **Tool component manifest** from the image actually built. Each entry binds
component and capability ids, distribution/source identity, exact version and bytes, dependency
lock, licence/notices, platforms, profiles, entrypoints and run-time dependencies to the image and
build-recipe digests. A hand-maintained package list is not this truth.

A reviewed **Tool capability manifest** describes each distinct operation: inputs and triggers,
canonical entrypoint and playbook pointer, supplying components and functional probes, authority and
network class, required handles, Resource envelope, risk, delivery status and equal-path replacement
group. It remains the stable queryable full surface.

For each Engagement the controller derives a compact **Tool view** with exact image, component,
capability, probe, isolation and view digests. It records four different facts per capability:

- `installed`: the exact bytes exist on this platform;
- `proved`: the required functional probe passed for this image and isolation/resource profile;
- `enabled`: the controller selected and exposed it for this Engagement; and
- `authorised`: a current identity-bound Capability handle permits the scoped operation now.

`authorised` implies all three earlier facts. Every false state has a typed reason such as
`not-in-image`, `probe-failed`, `profile-not-selected`, `handle-revoked`, `platform-unsupported` or
`boundary-refused`. Querying the state grants nothing and never scans `PATH`.

The initial Solve Lead prompt receives only the compact enabled slice: capability id, purpose,
trigger/input, canonical entrypoint or playbook pointer, current authority and bounds. Later Turns
receive only changed capability ids, old and new state/reason/bounds and the new Tool view digest.
Capability enforcement changes immediately; newly expanded authority starts at a Turn boundary.
A Specialist receives only the admitted requested Tool profiles. Triage receives no Challenge
tools. Recovery receives its fixed probe/remedy catalogue, never general solving authority. A typed
read-only query exposes the bounded full catalogue and unavailable reasons without enabling it.

Every Engagement records the component, capability, schema, Tool view and probe digests. Every Step
records the component/version/profile/handle it used. A Solve receipt binds the exact Tool profile
and versions; the Run capsule retains the referenced manifests and receipts rather than repeating
package dumps.

## Proof climbs from bytes to a full Run

Every functional result emits a probe receipt binding capability and component ids, image/platform,
fixture identities, provenance and licences, probe-harness and isolation/resource/network digests,
assertions, tool-reported versions, bounded outputs, resource use and a typed outcome. Any changed
identity makes the receipt stale. `unavailable` and `inconclusive` never become `pass`.

The proof ladder is:

1. **locked build closure** — reproduce arm64 and amd64 bytes; verify provenance, digests,
   licences/notices, installed inventory and absence of secrets or Run/corpus bytes; measure cached
   and uncached build time, compressed and uncompressed size and component/profile deltas;
2. **build-time functional probes** — pure computation and cheap inert parsers recover a semantic
   result from hashed, licence-compatible real inputs, never mere command/import presence;
3. **built-image admission probes** — the exact image exercises ptrace, hostile and dynamic foreign
   execution, loopback networking, browser/headless paths, resource cleanup and denied routes inside
   the actual isolation profile on both platforms for required components; an architecture-bound
   triggered component runs on every platform it claims and reports `platform-unsupported`
   everywhere else;
4. **representative local-Board Challenges** — the exact landed image works real Challenge and
   Target formats across every required capability family; and
5. **full-window pressure Runs** — 5.5-hour Runs combine Lanes and Specialists, legitimate long work
   and injected hangs, escapes, descendants and CPU/RAM/PID/disk/network pressure before release
   aggregation decides the freeze.

This ticket owns exact components, locks, manifests, the first three levels and the capability-to-
Challenge coverage matrix. [The local board: what it replicates, and what it must
not](https://github.com/jerome-queck/incypher-ctf/issues/158) owns the rig, fixtures, dynamic Board
behavior and execution of the last two levels. [What evidence makes v2 ready to
freeze](https://github.com/jerome-queck/incypher-ctf/issues/196) owns sample counts, comparators,
numeric thresholds, release aggregation and the verdict. A green image probe is necessary and never
the Gate by itself.

## Linux primitives own resources; a monitoring stack does not

The generic control surface explicitly names only four packages. `util-linux` supplies `unshare`,
`nsenter`, `lsns`, `setpriv`, `setsid` and `prlimit`; `mount` supplies the private bind/proc/tmpfs
operations when the launcher does not call the syscall directly. Both are already present in the
pinned arm64 and amd64 bases, so naming them makes their closure explicit at zero current marginal
image cost. `procps` supplies compact diagnostic `ps`, `pgrep`, `pmap`, `free` and `vmstat` views,
never ownership or kill correctness. `iproute2` supplies `ip` and `ss` for namespace route and socket
inspection. A 9 September arm64 install into the exact current image measured `procps 4.0.6-3` plus
`iproute2 7.1.0-1` and dependencies at **9,648,469 bytes** after apt indexes were removed;
`dpkg-query` reported 3,297 KiB and 4,734 KiB respectively before dependency/filesystem overhead.
The v2 locked build repeats marginal bytes and invocation overhead on both architectures rather than
treating this planning measurement as its final receipt.

`uidmap`, nftables or libseccomp enter only when the actual runtime profile proves that subordinate
ids, packet filtering or a linked syscall filter supplies a distinct missing primitive. Cgroup-tools,
process-name kill suites, systemd, Supervisor packages, `tini`, interactive monitors, telemetry
agents and Prometheus-style stacks stay out.

Packages cannot provide the actual boundary. The admitted runtime must prove a delegated writable
cgroup-v2 subtree, required controllers, user/PID/mount/network namespaces, private procfs, pidfds,
subreaping, process groups, signals and streaming pipe limits. Cgroup membership is process
ownership; a process group is attribution and convenience. Teardown uses `cgroup.kill`, waits for
`cgroup.events` to report `populated 0`, reaps descendants and removes the cgroup. `pkill`, `killall`
or `pstree` never decide membership. Missing delegation or isolation causes pre-Run Refusal, not a
weaker fallback.

Resource probes include a double-forked, `setsid`, TERM-ignoring process which forks during teardown;
PID, memory and CPU pressure against their cgroup ceilings; proc/cgroup counter reconciliation;
bounded infinite stdout/stderr with inherited pipes; denied Board/control/private/public routes; and
complete namespace, mount, process and cgroup disappearance after each Attempt. Full-window evidence
samples controller transitions and five-second intervals, bursting to one second during faults as
ADR-0045 requires.

## Size is measured against the machine, not guessed in advance

There is no arbitrary package-count proxy. The immutable image, stable rollback image,
physical free space and the post-merge cleanup lifecycle must remain observable; old ADR-0057 size
values are advisory benchmarks inside the configured Colima runtime. The build reports component/profile deltas, compressed and uncompressed size,
cached/uncached time and start cost; the freeze decision sets numeric thresholds from those
measurements. Run-time writable caches, profile scratch and outputs remain attempt-local and quota-
bounded. Image breadth never authorises loaded processes to consume memory or CPU outside their
Resource envelopes.

## Consequences

- The final image may be large, but every byte has a free provenance, a distinct capability, a
  functional receipt and one stored copy.
- A Tool component can be installed yet invisible, or visible yet unauthorised. This is deliberate:
  filesystem presence and effect authority are different facts.
- A failed component probe can leave a capability visibly unavailable. That is more useful than a
  green build which teaches an Agent to call something broken.
- v2 loses arbitrary live package installation and gains reproducibility, cross-Attempt isolation
  and a Tool view which does not drift after startup.
- The official ADK can bind architecture, artifact format or allowed network installation in v3;
  it cannot retroactively turn an unproved or non-free component into v2 evidence.
