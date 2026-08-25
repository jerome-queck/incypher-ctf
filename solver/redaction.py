"""Taking known credentials out of an Observation, before it is ever written.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) puts redaction at
write time rather than at publication: `/state` lives on a laptop and gets copied, zipped and
pasted into issues, so treating it as clean because it is untracked is how the leak happens anyway.

**Exact known values only.** The Solver was handed every secret it holds, so there is nothing to
infer — and a regex for a key shape would eat challenge output that merely looks like a key, which
is the worse failure of the two because nothing detects it. The encoded forms below are not
heuristics: they are the same value, written the way a verbose HTTP call writes it.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Mapping

from solver.credentials import SECRETS


# A base64 fragment shorter than this matches ordinary text, and an Observation eaten by a false
# positive is the failure nothing detects. Every declared secret is a credential, so its encodings
# are far longer than this; the floor exists so that a hypothetical short one costs coverage of its
# *encoded* forms rather than costing the whole stream. The value itself is always matched raw.
MIN_ENCODED_FRAGMENT = 8

# What the first characters of a base64 group say about the bytes under them, per starting offset.
# At offset 1 the first two characters are shared with whatever came before; at offset 2, three are.
_SHARED_WITH_WHAT_CAME_BEFORE = (0, 2, 3)


def _base64_forms(raw: bytes) -> set[bytes]:
    """The value as it appears **inside** a larger base64 blob, at each of the three alignments.

    Base64 encodes three bytes at a time, so `b64encode(secret)` occurs in
    `b64encode("user:" + secret)` only when the prefix length happens to divide by three — which is
    exactly the Basic-auth header this module's docstring cites, silently unmatched. Encoding the
    secret at each offset and dropping the characters that neighbouring bytes have a say in leaves
    the part that is the secret's alone. Up to two characters at each end stay in the clear, which
    is under two bytes of it and is the cost of matching the other forty.
    """
    forms = set()
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for offset in range(3):
            encoded = encode(b"\0" * offset + raw).rstrip(b"=")
            head = _SHARED_WITH_WHAT_CAME_BEFORE[offset]
            # A trailing partial group is finished by bytes that follow the secret, so its last
            # character is theirs as much as ours.
            tail = len(encoded) - (0 if (offset + len(raw)) % 3 == 0 else 1)
            if len(fragment := encoded[head:tail]) >= MIN_ENCODED_FRAGMENT:
                forms.add(fragment)
    return forms


def _forms(value: str) -> set[bytes]:
    """Every spelling of one secret that could reach an Observation.

    `curl -v` is the case that makes this more than paranoia: a token travels URL-encoded in a
    query string and base64 in a Basic-auth header, and neither matches the value we were handed.
    """
    raw = value.encode()
    return {
        raw,
        urllib.parse.quote(value, safe="").encode(),
        urllib.parse.quote_plus(value).encode(),
    } | _base64_forms(raw)


class Redactor:
    """The declared secrets, in every form they could be written, and what replaces them.

    The marker names the *variable* and never the value, so a post-mortem can see which credential
    reached an Observation without the record becoming the leak it was preventing.
    """

    def __init__(self, secrets: Mapping[str, str]) -> None:
        # A blank value is not a secret to hide, it is a slot nobody filled — and `--env-file`
        # exports one (`docs/credentials.md`). Taken as a value it would match between every pair
        # of characters in the stream and replace the whole run with markers.
        forms = {form: name for name, value in secrets.items() if value.strip() for form in _forms(value)}
        # Longest first, so two declared values sharing a prefix cannot leave the longer one's tail
        # in the clear.
        self._forms = sorted(forms.items(), key=lambda pair: len(pair[0]), reverse=True)

    @classmethod
    def for_declared_secrets(cls, environ: Mapping[str, str]) -> Redactor:
        """The redactor the Solver runs with: every name `solver/credentials.py` declares.

        Reading the declared set rather than a list at the call site is what makes ADR-0010's rule
        hold — a credential added there is covered here without anyone remembering to.
        """
        return cls({name: environ[name] for name in SECRETS if name in environ})

    def redact(self, body: bytes | str) -> bytes:
        """The body with every known credential replaced. Bytes in, bytes out.

        An Observation is whatever a command wrote, and a carved archive or a core dump is not
        text — decoding to redact would make this the step that destroyed it.
        """
        redacted = body.encode() if isinstance(body, str) else body
        for form, name in self._forms:
            if form in redacted:
                redacted = redacted.replace(form, f"[redacted:{name}]".encode())
        return redacted
