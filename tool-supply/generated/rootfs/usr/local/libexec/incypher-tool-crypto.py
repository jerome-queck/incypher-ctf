"""Bounded Crypto profile adapters exercised through Capability handles."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from Cryptodome.Cipher import AES
from fpylll import IntegerMatrix, LLL
import gmpy2
from sympy.ntheory.modular import crt


def request(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("Crypto request must be an object")
    return value


def encoding(path: str) -> list[str]:
    value = request(path)
    if set(value) != {"base64", "hex"}:
        raise ValueError("invalid encoding request")
    return [
        "base64=" + base64.b64decode(str(value["base64"]), validate=True).decode(),
        "hex=" + bytes.fromhex(str(value["hex"])).decode(),
    ]


def number_theory(path: str) -> list[str]:
    value = request(path)
    inverse = value["inverse"]
    if not isinstance(inverse, dict):
        raise ValueError("inverse must be an object")
    answer, modulus = crt(value["moduli"], value["residues"])
    inverted = gmpy2.invert(int(inverse["value"]), int(inverse["modulus"]))
    return [f"crt={int(answer)} modulus={int(modulus)} inverse={int(inverted)}"]


def symmetric_hash(path: str) -> list[str]:
    value = request(path)
    decrypted = AES.new(bytes.fromhex(str(value["aes_material_hex"])), AES.MODE_ECB).decrypt(
        bytes.fromhex(str(value["ciphertext"]))
    )
    padding = decrypted[-1]
    if padding < 1 or padding > AES.block_size or decrypted[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid PKCS#7 padding")
    plaintext = decrypted[:-padding]
    digest = hashlib.sha256(plaintext).hexdigest()
    if digest != value["sha256"]:
        raise ValueError("hash mismatch")
    return ["plaintext=" + plaintext.decode(), "sha256=" + digest]


def classical(path: str) -> list[str]:
    value = request(path)
    shift = int(value["shift"])
    plaintext = "".join(
        chr((ord(character) - ord("A") - shift) % 26 + ord("A")) if character.isupper() else character
        for character in str(value["ciphertext"])
    )
    return ["plaintext=" + plaintext]


def asymmetric(path: str) -> list[str]:
    value = request(path)
    prime_p, prime_q = int(value["p"]), int(value["q"])
    modulus = prime_p * prime_q
    private = pow(int(value["e"]), -1, (prime_p - 1) * (prime_q - 1))
    decoded = pow(int(value["ciphertext"]), private, modulus)
    plaintext = decoded.to_bytes((decoded.bit_length() + 7) // 8, "big").decode()
    return [f"modulus={modulus}", "plaintext=" + plaintext]


def cas(path: str) -> list[str]:
    value = request(path)
    if set(value) != {"coefficients"}:
        raise ValueError("invalid CAS request")
    coefficients = value["coefficients"]
    if (
        not isinstance(coefficients, list)
        or not 2 <= len(coefficients) <= 64
        or any(not isinstance(item, int) or isinstance(item, bool) or abs(item) > 2**63 for item in coefficients)
    ):
        raise ValueError("CAS coefficients must be bounded integers")
    program = (
        "import json,os; coefficients=json.loads(os.environ['INCYPHER_CAS_COEFFICIENTS']); "
        "x=polygen(QQ); polynomial=sum(QQ(value)*x**power for power,value in "
        "enumerate(reversed(coefficients))); print(sorted(polynomial.roots(multiplicities=False)))"
    )
    completed = subprocess.run(
        ["/usr/local/sage/bin/sage", "-c", program],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "DOT_SAGE": "/tmp/incypher-sage",
            "HOME": "/tmp/incypher-sage",
            "TMPDIR": "/tmp",
            "INCYPHER_CAS_COEFFICIENTS": json.dumps(coefficients, separators=(",", ":")),
        },
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Sage evaluation failed")
    return ["roots=" + completed.stdout.strip()]


def key_certificate(path: str) -> list[str]:
    arguments = [
        "openssl",
        "x509",
        "-in",
        path,
        "-noout",
        "-subject",
        "-issuer",
        "-serial",
        "-fingerprint",
        "-sha256",
    ]
    completed = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()


def lattice(path: str) -> list[str]:
    rows = request(path)["basis"]
    if not isinstance(rows, list):
        raise ValueError("basis must be a matrix")
    basis = IntegerMatrix.from_matrix(rows)
    LLL.reduction(basis)
    return ["shortest=" + json.dumps(list(basis[0]), separators=(",", ":"))]


def password(path: str) -> list[str]:
    root = Path(path)
    john_home = Path("/tmp/incypher-john")
    john_home.mkdir(exist_ok=True)
    environment = {**os.environ, "HOME": str(john_home), "TMPDIR": "/tmp"}
    generated = subprocess.run(
        ["/usr/sbin/john", "--stdout", "--wordlist=" + str(root / "wordlist.txt")],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if generated.returncode:
        raise RuntimeError(generated.stderr.strip() or "John candidate generation failed")
    candidates = generated.stdout.splitlines()
    expected = (root / "hash.txt").read_text().split(":", 1)[1].strip()
    plaintext = next(value for value in candidates if hashlib.md5(value.encode()).hexdigest() == expected)
    return ["plaintext=" + plaintext]


HANDLERS = {
    "crypto.asymmetric": asymmetric,
    "crypto.cas": cas,
    "crypto.classical": classical,
    "crypto.encoding": encoding,
    "crypto.certificate": key_certificate,
    "crypto.lattice": lattice,
    "crypto.number-theory": number_theory,
    "crypto.hash-crack": password,
    "crypto.symmetric-hash": symmetric_hash,
}


def main(arguments: list[str]) -> int:
    if arguments and arguments[0] == "--self-check":
        subprocess.run(["/usr/local/sage/bin/sage", "--version"], check=True, capture_output=True)
        subprocess.run(["/usr/sbin/john", "--list=build-info"], check=True, capture_output=True)
        try:
            cas("/opt/solver/tool-supply/tool-crypto/cas-injection.json")
        except ValueError:
            pass
        else:
            raise RuntimeError("CAS injection fixture was accepted")
        return 0
    if len(arguments) != 2 or arguments[0] not in HANDLERS:
        return 64
    capability, input_path = arguments
    print(f"schema=tool-crypto.{capability.removeprefix('crypto.')}.v1")
    for fact in HANDLERS[capability](input_path):
        print(fact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
