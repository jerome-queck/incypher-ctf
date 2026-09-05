# Healthcare knowledge a general CTF Solver can recognise

_Research snapshot: 5 September 2026. This answers the Wayfinder research question with a
capability map, not an architecture decision. Sources are standards owners, public authorities,
official datasets, and tool maintainers. “Plausible” means useful for a healthcare-dressed CTF;
it does not mean IN-CYPHER has announced such a Challenge._

## Decision-grade answer

The useful preparation is **format and system literacy, not clinical expertise**. A Solver should
be able to recognise and inspect four high-value surface families:

1. **clinical exchange** — FHIR JSON/XML and SMART-on-FHIR OAuth, HL7 v2 delimited messages, and
   C-CDA XML;
2. **medical imaging** — DICOM Part 10 files, raw DICOM datasets, DICOM network services, and
   DICOMweb;
3. **connected-device artefacts** — embedded firmware, update bundles, configurations, packet
   captures, debug interfaces, wireless/network traces, certificates, logs, and software bills of
   materials;
4. **health data and code systems** — patient/encounter/observation tables, physiological
   waveforms, and identifiers from LOINC, SNOMED CT, ICD, and RxNorm.

Those surfaces still reduce to ordinary CTF work: web/API authorization, pwn/reversing of a
service or firmware, crypto/protocol analysis, or file/network forensics. Domain recognition
should enrich recon and search terms; it must not override the Board's Category or make a format
label evidence of a vulnerability.

The smallest justified offline capability addition is a **recognition pack**: magic/signature and
structural detectors, DICOM metadata/pixel extraction, FHIR/HL7/C-CDA parsing and validation,
WFDB waveform reading, and local terminology indexes built only from redistributable data. Large
clinical corpora, full terminology releases, Java servers, and DICOM/PACS labs are useful Gate
fixtures, not default context or necessarily default image packages.

## Evidence boundary: what IN-CYPHER actually says

Official evidence supports only a broad scenario prior:

- The [IN-CYPHER programme][incypher-programme] is about protecting health data and connected
  diagnostic, therapeutic, wearable, and implantable devices. It explicitly highlights device
  connectivity, long lifespans, difficult updates, vulnerability discovery, and the possible
  effect of compromise on patient care.
- The [official hackathon page][incypher-hackathon] says Challenges span classic CTF categories
  including web, pwn, crypto, reverse engineering, and forensics, with several set in medical
  scenarios. The [platform guide][incypher-how-to] exposes only generic static/isolated, raw-TCP,
  web, deployment, and submission mechanics.
- The adjacent official conference themes add implantable devices, connected wearables and
  healthcare systems, and security/privacy/provenance algorithms
  ([conference page][incypher-conference]). They describe the programme, not the unpublished
  Challenge batch.

There is **no official evidence at this snapshot** that the first batch uses FHIR, HL7, DICOM,
SNOMED CT, any named device, any named dataset, any named hospital system, or any particular
vulnerability. The map below ranks ecosystem plausibility, not organiser intent. It must not be
used to pre-submit guessed flags, target real healthcare systems, or search for copied solutions.

## Three evidence rings

The rings describe **distance from published IN-CYPHER facts**, not probability that a Challenge
will appear. Confidence is tracked separately:

- **Ring 1 — explicit anchor:** an organiser-controlled source directly says it. Confidence is
  high for the statement and zero for any unpublished implementation detail.
- **Ring 2 — directly adjacent capability:** a standards owner, regulator, public authority,
  official dataset, or tool owner establishes a real healthcare surface that directly fits one or
  more Ring 1 anchors. Confidence is high that the surface exists and is worth recognising, but
  unknown that IN-CYPHER uses it.
- **Ring 3 — edge watchlist:** a specialist, jurisdiction-specific, or more weakly adjacent
  healthcare surface. It may be outside the batch. Prepare only a name/signature/tool pointer and
  fetch it on demand after Challenge evidence appears.

Within Rings 2 and 3, priority means preparation value before the batch: `A` is compact and
broadly useful; `B` is useful but heavier or narrower; `C` is on-demand only. Recognition clues
are hypotheses to test against bytes, not sole classifiers.

### Ring 1 — explicit IN-CYPHER anchors

| Published anchor | What it warrants preparing | What it does **not** warrant |
| --- | --- | --- |
| Several Challenges use medical scenarios over classic web, pwn, crypto, reversing and forensics categories ([hackathon page][incypher-hackathon]) | A healthcare clue-to-ordinary-category translation layer; parsers and vocabulary augment the existing CTF toolbox | A healthcare Category, a named standard, or a predicted Challenge |
| The programme protects health data and connected diagnostic, therapeutic and implantable devices ([programme][incypher-programme]) | Recognition of structured health data, device firmware, update, network, wireless and telemetry artefacts | Any named device, vendor, protocol, file format or vulnerability |
| Devices may be current, future or legacy, may be difficult to update, and compromise may affect care ([programme][incypher-programme]) | Legacy component/version inventory, update authenticity/rollback analysis, safety and availability consequences | Permission to touch real equipment or assume a firmware Challenge |
| Programme themes include connected wearables/healthcare systems and security, privacy and provenance ([conference page][incypher-conference]) | Web/API, network, identity, confidentiality, integrity, audit and provenance literacy across device-to-system flows | Conference research topics as a Challenge list |
| The Board serves downloadable/shared or isolated raw-TCP/web targets and requires autonomous operation ([platform guide][incypher-how-to]) | Offline file triage plus Challenge-only TCP/HTTP analysis inside the existing Target boundary | A special healthcare transport, access to external systems, or organiser endorsement of any tool below |

### Rings 2 and 3 — capability map

| Ring / priority | Surface and role | Recognition clues | What the Solver needs to do offline | Recurrent security questions | Primary references |
| --- | --- | --- | --- | --- | --- |
| 2 / A | **FHIR REST/API** — interoperable resources such as `Patient`, `Encounter`, `Observation`, `DiagnosticReport`, `MedicationRequest`, and `Binary` | JSON root `resourceType`; XML in the FHIR namespace; `application/fhir+json` or `application/fhir+xml`; `/metadata`; resource URLs shaped `<base>/<type>/<id>` | Pretty-print without losing arrays; enumerate Bundle entries/references; follow contained and external references; extract attachments; query with `jq`; validate against the declared FHIR version/profile when packages are available | Object/patient-level authorization, excessive SMART scopes, bearer-token leakage, insecure attachments, CORS, error disclosure, provenance/audit gaps, version/profile confusion | [FHIR formats][fhir-formats], [FHIR REST][fhir-http], [FHIR security][fhir-security] |
| 2 / A | **SMART App Launch** — OAuth 2.0/OIDC authorization around FHIR | `/.well-known/smart-configuration`, `authorization_endpoint`, `token_endpoint`, `jwks_uri`, scopes beginning `patient/`, `user/`, `system/`, `launch`, `openid`, `fhirUser`; `iss`, `launch`, `aud`, PKCE fields | Parse discovery and JWTs locally; compare requested/granted scopes, issuer, audience, redirect URI, PKCE method, token expiry, and launch context; never replay live credentials outside the Challenge | Redirect/audience validation, scope overbreadth, missing PKCE, stale refresh tokens, confused patient/encounter context, asymmetric-client key handling | [SMART overview][smart-overview], [SMART launch][smart-launch], [SMART conformance][smart-conformance] |
| 2 / A | **HL7 v2 messages** — long-lived event feeds between admission, laboratory, pharmacy, and other systems | `MSH` header; commonly `MSH|^~\&`; three-character segments such as `PID`, `PV1`, `ORC`, `OBR`, `OBX`, `MSA`; carriage-return segment terminators; message types such as ADT/ORM/ORU | Preserve raw delimiters and escapes; split by the MSH-declared characters, not a hard-coded pipe; enumerate patient IDs, message-control IDs, orders, observations, acknowledgements, character set, and version; inspect framing in captures | Unauthenticated trusted feeds, parser/delimiter confusion, injection through free text or locally defined escapes, replay/duplicate control IDs, PHI in logs, weak network segmentation | [HL7 v2.9 control and encoding][hl7-v2-control], [HL7 observations][hl7-v2-observations] |
| 2 / A | **DICOM files and datasets** — imaging objects plus extensive metadata and sometimes pixels, waveforms, overlays, documents, or encapsulated media | Part 10 files have a 128-byte preamble followed by `DICM`, File Meta Information, then a dataset; extensions are unreliable; a raw dataset may lack the preamble/prefix; tags appear as `(gggg,eeee)` with a Value Representation | Run `dcmdump`/`pydicom`; report transfer syntax, SOP class, study/series/instance UIDs, patient and institution fields, private tags, sequences, overlays, pixel data, embedded documents, and hashes; render pixels separately; retry suspected raw datasets explicitly rather than treating absent `DICM` as disproof | Residual PHI/private tags, burned-in or overlay text, inconsistent pseudonyms across instances, malicious compressed pixels or parsers, UID correlation, unsigned/tampered objects | [DICOM file format][dicom-file], [DICOM confidentiality profiles][dicom-confidentiality], [pydicom][pydicom] |
| 2 / A | **DICOM services / PACS** — archive, query/retrieve, storage, and modality worklists | DIMSE names such as C-ECHO, C-FIND, C-MOVE, C-GET, C-STORE; AE Titles; default ecosystem port 104 is a clue only; DICOMweb paths `/studies`, `/series`, `/instances`; QIDO-RS, WADO-RS, STOW-RS | Decode pcaps; enumerate presentation contexts, AE titles, UIDs and operations; use `pynetdicom` or DCMTK against Challenge-only endpoints; use ordinary HTTP tooling for DICOMweb and retain response media types | Missing auth/TLS, broad query/retrieve, store abuse, cross-patient IDOR, SSRF/path traversal in archives/viewers, exposed admin panels, metadata leakage | [DICOM network services][dicom-network], [DICOMweb Studies service][dicomweb] |
| 2 / A | **Device firmware and update surface** — implantable, wearable, diagnostic, therapeutic, monitoring, gateway, and controller software | Firmware/update archives, manifests, signatures, certificates, squashfs/cramfs/JFFS2, bootloaders, ELF binaries, web assets, serial strings, default configs, SBOMs, vendor protocols, debug output | Apply the normal firmware cascade; preserve signatures and manifests before extraction; inventory components/versions/CVEs offline; locate network services, update verification, credentials, keys, debug interfaces, and rollback/version checks; emulate only inside the Challenge sandbox | Hard-coded/default credentials, active debug code, unsigned or downgradeable firmware, exposed root shell, unsafe update/recovery, weak certificate/key lifecycle, memory corruption, resource exhaustion | [FDA 2025 guidance][fda-guidance], [FDA cybersecurity hub][fda-cyber], [CISA patient-monitor advisory][cisa-contec] |
| 2 / A | **Health data tables and databases** — EHR/HIS, clinic, laboratory, pharmacy, scheduling, billing, and research exports | CSV/TSV/NDJSON, SQLite/PostgreSQL dumps, UUIDs/MRNs, dates, encounters, observations, medications, claims, audit tables; healthcare codes mixed with local codes | Inspect schema, foreign keys, distributions, timestamps and audit history; distinguish patient, encounter, specimen, order and observation identity; identify code-system URIs before translating values; detect archive/date-shift artifacts | PHI/PII exposure, weak tenant/patient boundaries, SQL injection, insecure backups, predictable identifiers, audit alteration, inconsistent deletion/pseudonymization | [Synthea exports][synthea], [CMS synthetic claims][cms-synpuf], [NIST ePHI guide][nist-hipaa] |
| 2 / B | **C-CDA / CDA clinical documents** — XML clinical summaries and reports | XML `ClinicalDocument`, HL7 v3 namespace `urn:hl7-org:v3`, template IDs, coded sections, human-readable `text`, embedded Base64 objects | Use an XML-aware parser with external entity/network access disabled; enumerate template IDs, identifiers, sections, codes, references, signatures, and embedded media; compare structured entries with narrative | XXE/unsafe XSLT in generic tooling, hidden data in narrative/attachments, signature/canonicalization confusion, residual identifiers | [C-CDA specification][ccda] |
| 2 / B | **Physiological waveforms** — ECG, EEG, bedside monitor, wearable, and telemetry records | WFDB record groups such as `.hea` plus `.dat` and annotation files; EDF/EDF+ headers; DICOM waveform objects; sample rate, lead/channel names and units | Read metadata and channel layout first; plot and filter deterministically; inspect annotations and timing; test for payloads in headers, unused channels, least-significant bits, or appended bytes without assuming steganography | Patient identifiers in headers, manipulated calibration/timestamps, integrity/provenance gaps, parser bugs, covert data in metadata or samples | [WFDB files][wfdb-files], [WFDB software][wfdb], [DICOM waveform model][dicom-waveform] |
| 2 / B | **Terminology services and release files** — translate machine codes, not diagnose patients | Coding triples (`system`, `code`, `display`); SNOMED numeric identifiers; LOINC hyphenated numeric codes; ICD alphanumeric classifications; RxNorm RXCUI/RRF files | Identify the owning system/version; perform exact local lookup; retain the original code/display and source/version; traverse mappings only when the question requires it; never infer a diagnosis from a bare identifier | Code/display mismatch, obsolete/inactive codes, malicious or ambiguous local mappings, version drift, terminology-server injection, licence leakage | [SNOMED model][snomed], [LOINC guide][loinc], [WHO ICD][icd], [RxNorm overview][rxnorm], [UMLS Metathesaurus][umls] |
| 2 / B | **Audit, provenance, consent, and security labels** — accountability attached to health data | FHIR `AuditEvent`, `Provenance`, `Consent`, `meta.security`; DICOM audit/security profiles; application audit trails and access reasons | Build a time-ordered actor/action/object view; verify references and signatures; distinguish creation, access, export, amendment and deletion; preserve clock/time-zone evidence | Log gaps/tampering, spoofed actor identity, over-broad break-glass access, missing consent enforcement, security-label stripping, unsynchronised clocks | [FHIR security][fhir-security], [DICOM security][dicom-security], [NIST ePHI guide][nist-hipaa] |
| 2 / B | **Medical-device network and wireless captures** — device-to-gateway, monitor-to-central station, maintenance, cloud, and mobile-app links | DNS/mDNS, DHCP, HTTP(S), MQTT, BLE GATT, serial-over-IP, DICOM, HL7, proprietary binary frames, periodic telemetry and update checks; a healthcare label does not establish a protocol | Start with generic `tshark`, TLS/certificate, flow and entropy analysis; extract protocol fields and payloads; correlate device IDs/timestamps across logs; reverse proprietary framing from observed messages; do not transmit to non-Challenge devices | Cleartext PHI, unauthenticated commands, replay, weak pairing, static keys, certificate validation failures, insecure cloud/API binding, IT/OT pivot paths | [IN-CYPHER programme][incypher-programme], [FDA guidance][fda-guidance], [Singapore MDOTS][sg-mdots] |
| 3 / C | **Claims and administrative interchange** — payer/provider transactions and remittance | X12-like `ISA`/`GS`/`ST` envelopes or CMS fixed/delimited files; diagnosis/procedure/provider/member identifiers; 837/835/270/271 names are US-specific clues | Identify format and companion-guide version before parsing; retain envelope/control numbers; use `pyx12` offline for supported HIPAA X12 versions and synthetic fixtures; cross-reference only needed code sets | PHI leakage, predictable IDs, duplicate/replayed transactions, malformed-envelope parser bugs, insecure file transfer | [X12 transaction sets][x12-transactions], [pyx12][pyx12], [CMS synthetic claims][cms-synpuf] |
| 3 / C | **Genomic and pathology artefacts** — sequencing reads, variants, slides, and analyser outputs | FASTA/FASTQ, SAM/BAM/CRAM, VCF/BCF, large tiled images, DICOM pathology, instrument run folders | Detect by format-specific headers; use `samtools`/`bcftools`/HTSlib for structure; inspect metadata, sample names, checksums and appendages; avoid loading huge files into model context | Identifying genomic data, sample swaps, integrity/provenance, crafted parser input, embedded credentials/paths in pipelines | [GA4GH hts-specs][hts-specs], [DICOM pathology][dicom-pathology] |

Ring 3 is intentionally bounded to these two pointers. Nothing is installed or preloaded for them
unless an actual Challenge exposes a matching artefact; additional specialist domains discovered
later join the batch-specific record, not this pre-release prior.

## Systems map: names are navigation, not separate attack categories

These common labels help interpret a scenario and choose reference material:

| System name | Typical responsibility | Likely standards/data seams |
| --- | --- | --- |
| EHR/EMR, clinic management system, patient portal | Longitudinal record, clinician workflow, patient access | FHIR/SMART, C-CDA, HL7 v2, web APIs, relational databases |
| HIS / integration engine / HIE | Routes data across facilities and applications | HL7 v2, FHIR, files/SFTP, message queues, mappings |
| LIS | Orders, specimens, laboratory instruments and results | HL7 ORM/ORU, OBR/OBX, LOINC, CSV/vendor protocols |
| RIS / PACS / VNA / modality | Imaging worklist, acquisition, archive, viewer and exchange | DICOM DIMSE, DICOMweb, DICOM Part 10, HL7 orders/results |
| Pharmacy/eMAR | Medication vocabulary, orders, dispensing and administration | RxNorm or local drug codes, FHIR Medication resources, HL7 v2 |
| Patient monitor / central station | Vital signs, alarms, waveforms and device control | Proprietary telemetry, IEEE 11073-family concepts, HL7/DICOM waveform export, web/mobile gateways |
| Infusion, implantable or wearable device | Sensing and/or therapy under safety and power constraints | Firmware, BLE/RF/proprietary protocols, phone/programmer/cloud gateway, update packages |
| Billing/claims | Eligibility, claims and remittance | X12/CMS-specific formats, ICD/procedure codes, flat files/databases |

Do not assume these boundaries are clean. An image viewer can be a web application; a modality can
embed Windows or Linux; an EHR export can contain DICOM or Base64 attachments; an integration
engine can bridge HL7 v2 into FHIR; and a patient monitor can expose an ordinary web admin panel.

## Security patterns worth recognising

The pattern set is ordinary cybersecurity with healthcare consequences and long-lived-device
constraints. The [FDA guidance][fda-guidance], [Singapore CLS(MD)][sg-cls-md],
[Singapore MDOTS][sg-mdots], and [HHS healthcare goals][hhs-cpg] converge on these themes:

- **Identity and least privilege:** unique credentials, authenticated users/devices, authorization
  on every patient/object operation, minimal scopes, controlled privileged or break-glass access.
- **Trust boundaries and segmentation:** separate device/OT, clinical application, management,
  vendor remote-access, and ordinary enterprise networks; enumerate every external system and
  data flow.
- **Secure communications and cryptographic lifecycle:** TLS where applicable, certificate and key
  provisioning/rotation, authenticated updates, signature verification, replay protection, and
  clock integrity.
- **Input and parser safety:** health formats are nested, versioned, extensible, and may encapsulate
  compressed pixels, XML, JSON, documents, or private/vendor fields. Keep generic archive/parser
  hardening and resource limits in scope.
- **Updateability and legacy operation:** inventory versions/components, make an SBOM usable,
  mitigate known vulnerabilities, prevent rollback, support secure recovery, and distinguish a
  supported current device from a legacy device that cannot be patched normally.
- **Availability and patient safety:** denial of service, unsafe commands, modified measurements,
  delayed care, and loss of alarm/therapy availability can matter more than record theft. Never
  exercise real devices or systems; Challenge isolation is the authorization boundary.
- **Data minimisation, confidentiality, and provenance:** health data can remain identifying in
  metadata, pixels, waveforms, free text, linkage keys, and audit logs even after obvious names
  are removed. Preserve evidence; do not propagate real PHI into prompts or fixtures.
- **Monitoring, audit, recovery, and disclosure:** retain actor/action/object/time evidence,
  detect anomalous access or device behavior, recover safely, and report vulnerabilities through
  the applicable authorised process.

One concrete official example keeps the list grounded: CISA's advisory for a patient monitor
records hard-coded credentials, active debug code, an unprotected channel, unbounded resource use,
root-shell access, and firmware modification without adequate controls
([CISA advisory][cisa-contec]). This proves those patterns occur in medical products; it says
nothing about the IN-CYPHER batch.

## Offline tool and fixture plan

### Recognition pack: small, deterministic, broadly reusable

The existing image already has `file`, `strings`, `xxd`, `binwalk3`, `exiftool`, `jq`, Python,
OpenSSL, archive tools, and the ordinary forensic stack. Healthcare preparation should first add
or make reproducibly fetchable:

| Need | Suitable offline tool | Probe that demonstrates capability |
| --- | --- | --- |
| DICOM structure and metadata | `dcmdump` from DCMTK and Python `pydicom` | Parse one Part 10 file and one raw dataset; print transfer syntax, SOP class, UIDs, selected/private tags; extract/render a pixel object |
| DICOM networking | `pynetdicom` or DCMTK `echoscu`/`findscu`/`storescu` | Against a local fixture only, negotiate an association, C-ECHO, query one study, store/retrieve one object, capture the traffic |
| FHIR JSON/XML | `jq`, hardened XML parser, pinned FHIR definitions; optionally the official Java validator as a Gate-side tool | Detect version/resource type, enumerate Bundle references and attachments, validate a valid/invalid R4 fixture fully offline |
| HL7 v2 | a small delimiter-aware parser plus raw-byte view; optionally `hl7apy` | Parse CR-delimited messages with non-default delimiters, escapes, repeating fields and an acknowledgement; preserve byte offsets |
| C-CDA/XML | `xmllint` or equivalent with network/entity expansion disabled | Extract template IDs, patient/encounter identifiers, sections and one Base64 attachment without external fetches |
| Physiological records | PhysioNet `wfdb` tools/Python package | Read a small open WFDB record, list signals/units/rates/annotations, produce a plot and verify sample hashes |
| Terminology lookup | SQLite indexes generated from explicitly licensed/pinned source releases | Resolve a small redistribution-safe LOINC/RxNorm fixture while returning source, version, code and display; unknown codes remain unknown |
| Packet captures | `tshark`/Wireshark CLI plus ordinary TCP/TLS tools | Extract flows, certificates and recognized HL7/DICOM fields from a synthetic local capture; retain raw pcap hash |

Every detector should emit `format`, `confidence`, `evidence offsets/fields`, `version`, and
`next tool`. It should not emit “healthcare vulnerability found.” A failed strict parser should
preserve bytes and explain whether the cause is malformed input, unsupported version/transfer
syntax, missing optional codec, or merely a nonconformant/raw representation.

### Gate-side labs, not necessarily image dependencies

- A local FHIR R4 server with SMART-like authorization fixtures: one correctly scoped client and
  deliberately vulnerable Challenge-only variants.
- A local DICOM archive/server with DIMSE and DICOMweb, synthetic studies, private tags, overlays,
  burned-in text, and mixed transfer syntaxes.
- A tiny HL7 v2 sender/receiver fixture carrying synthetic ADT, order and result messages, with
  framing, acknowledgements and delimiter edge cases.
- Firmware/update specimens created for the Gate: signed and unsigned manifests, downgrade case,
  hard-coded test credentials, debug string, and a known vulnerable parser/service.
- Small public/synthetic health records and waveforms with exact checksums and licences. Do not
  bake large corpora into the Solver or silently download them during a Run.

## Public data: what can safely become a fixture

| Source | Useful shape | Access/licence boundary | Recommended use |
| --- | --- | --- | --- |
| [Synthea][synthea] | Synthetic longitudinal records in FHIR R4/STU3/DSTU2, C-CDA and CSV | The project states its generated data is synthetic and free of privacy/security restrictions | Best general-purpose patient/encounter/observation fixture; pin generator/version, seed and output hash |
| [CMS Medicare SynPUF][cms-synpuf] | Synthetic beneficiary, inpatient, outpatient, carrier and prescription-claim-like files | Free public download; CMS warns synthetic files have limited inferential research value | Claims/schema parsing fixture, never clinical truth |
| [PhysioNet MIT-BIH / WFDB][wfdb] | Open ECG signals, headers and annotations | Dataset-specific licences still apply; small records are sufficient | Waveform recognition, plotting, timing and annotation fixtures |
| [MIMIC-IV][mimic] | Rich deidentified hospital, ICU, ED, note and linked imaging data | Credentialed users, training and signed data-use agreement; access may not be shared; derivatives remain sensitive | Do **not** bundle or hand to an autonomous team image. Use only if every operator is individually authorised and the licence permits the exact local experiment; Synthea normally removes the need |
| DICOM samples from the [pydicom project][pydicom] | Small known-good modality objects bundled for tests/examples | Follow the repository licence and sample provenance | Parser/rendering smoke tests; add purpose-built synthetic de-identification edge cases separately |

“Publicly discoverable” is not the same as redistributable. Each fixture needs source URL,
retrieval date, version, licence/terms, original hash, transformations, and final hash. Never try to
reidentify a person, bypass a dataset gate, or copy restricted health data into a commit, model
prompt, or Run trace.

## Terminology survival guide

Clinical codes usually answer “what does this field mean?”, not “where is the flag?”:

- **LOINC** identifies laboratory and clinical observations. Its official guide describes codes as
  universal identifiers for results used inside exchange standards such as HL7 and DICOM
  ([LOINC guide][loinc]).
- **SNOMED CT** represents clinical meanings as concepts with numeric identifiers, human-readable
  descriptions, and relationships. Editions and versions matter; national extensions and licence
  terms matter ([SNOMED model][snomed]).
- **ICD** is a WHO classification for recording/reporting diseases and health information, not a
  synonym dictionary for every clinical term. ICD-10 and ICD-11 must not be conflated
  ([WHO ICD][icd]).
- **RxNorm** normalizes generic and branded clinical-drug names and links drug vocabularies. NLM
  publishes releases and a REST API; its current prescribable subset has distinct access terms
  ([RxNorm overview][rxnorm]).
- **UMLS** links names and concepts across many source vocabularies, but source licences survive
  aggregation. A matching CUI does not erase source/version provenance
  ([UMLS Metathesaurus][umls]).

The correct lookup result is therefore a tuple such as
`(system URI/name, edition/version, code, display, status, source)`. A display string supplied by a
Challenge may disagree with the authoritative code; preserve both rather than silently correcting
the evidence.

## What not to prepare

- No guessed Challenge narratives, vendors, flags, endpoints, credentials, packet formats, or
  vulnerabilities.
- No scanning or interaction with real hospitals, clinics, devices, wearables, cloud tenants, or
  public FHIR/DICOM endpoints. Official Challenge targets only.
- No copied writeups or flags. General standards/examples are knowledge; event solutions are not.
- No diagnosis or treatment engine. Clinical decision support is irrelevant to recognising a CTF
  artefact and creates unsafe confidence.
- No giant terminology/corpus dump in model context. Query exact codes locally and return bounded
  provenance.
- No assumption that `.dcm`, `DICM`, `MSH|^~\&`, a familiar TCP port, or a FHIR-looking JSON key is
  conclusive. Combine structure, parser result, and surrounding evidence.
- No US-law default for a Singapore event. HIPAA sources here explain common ePHI security
  controls; Singapore's HIA, MDOTS and CLS(MD) are the more local policy context, and none predicts
  a Challenge.

## Uncertainties and batch-time update

Still unknown until official artefacts appear:

- which healthcare surfaces, if any, occur in the first batch;
- whether domain knowledge is merely narrative or required to parse/interpret bytes;
- the versions/profiles/code systems and whether specimens are valid, malformed, synthetic, or
  intentionally adversarial;
- whether a special SDK provides domain parsers; and
- whether image codecs, Java, packet dissectors, radio tooling, or hardware access are needed.

At batch release, classify each actual Challenge from its own files, text and endpoint. Add only
the missing parser/reference needed by observed evidence, pin its bytes, and run a benign
recognition probe before broad analysis. Record whether the domain clue changed a solve decision
or merely saved search time; that measurement decides which knowledge belongs permanently in the
image versus in a retrievable reference pack.

## Primary sources

[ccda]: https://hl7.org/cda/us/ccda/
[cisa-contec]: https://www.cisa.gov/news-events/ics-medical-advisories/icsma-22-244-01
[cms-synpuf]: https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-claims-synthetic-public-use-files
[dicom-confidentiality]: https://dicom.nema.org/medical/dicom/current/output/chtml/part15/chapter_E.html
[dicom-file]: https://dicom.nema.org/medical/dicom/current/output/chtml/part10/chapter_7.html
[dicom-network]: https://dicom.nema.org/medical/dicom/current/output/chtml/part07/chapter_9.html
[dicom-pathology]: https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_A.32.html
[dicom-security]: https://dicom.nema.org/medical/dicom/current/output/chtml/part15/
[dicom-waveform]: https://dicom.nema.org/medical/dicom/current/output/chtml/part03/chapter_A.html
[dicomweb]: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/chapter_10.html
[fda-cyber]: https://www.fda.gov/medical-devices/digital-health-center-excellence/cybersecurity
[fda-guidance]: https://www.fda.gov/media/119933/download
[fhir-formats]: https://hl7.org/fhir/R5/resource-formats.html
[fhir-http]: https://hl7.org/fhir/R5/http.html
[fhir-security]: https://hl7.org/fhir/R5/security.html
[hhs-cpg]: https://hhscyber.hhs.gov/cornerstone.html
[hl7-v2-control]: https://hl7.eu/HL7v2x/v29/std29/ch02.html
[hl7-v2-observations]: https://hl7.eu/HL7v2x/v29/std29/ch07.html
[hts-specs]: https://github.com/samtools/hts-specs
[icd]: https://www.who.int/standards/classifications/classification-of-diseases
[incypher-conference]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/conference/
[incypher-hackathon]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
[incypher-how-to]: https://hackathon.in-cypher.com/how-to-play
[incypher-programme]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/
[loinc]: https://loinc.org/kb/users-guide/introduction
[mimic]: https://physionet.org/content/mimiciv/
[nist-hipaa]: https://csrc.nist.gov/pubs/sp/800/66/r2/final
[pydicom]: https://github.com/pydicom/pydicom
[pyx12]: https://github.com/azoner/pyx12
[rxnorm]: https://www.nlm.nih.gov/research/umls/rxnorm/overview.html
[sg-cls-md]: https://www.csa.gov.sg/our-programmes/certification-and-labelling-schemes/cls-md/about/
[sg-mdots]: https://hpp.moh.gov.sg/healthtech-instruction-manual/
[smart-conformance]: https://hl7.org/fhir/smart-app-launch/STU2.2/conformance.html
[smart-launch]: https://hl7.org/fhir/smart-app-launch/STU2.2/app-launch.html
[smart-overview]: https://hl7.org/fhir/smart-app-launch/STU2.2/
[snomed]: https://docs.snomed.org/snomed-ct-practical-guides/snomed-ct-starter-guide/5-snomed-ct-logical-model
[synthea]: https://synthetichealth.github.io/synthea/
[umls]: https://www.nlm.nih.gov/research/umls/knowledge_sources/metathesaurus/index.html
[wfdb-files]: https://physionet.org/physiotools/wpg/wpg_38.htm
[wfdb]: https://physionet.org/content/wfdb/
[x12-transactions]: https://x12.org/products/transaction-sets
