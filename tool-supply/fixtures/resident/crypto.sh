# shellcheck shell=sh
set -eu
case "${1:-}" in
  --version) printf '1.1.0\n'; exit 0 ;;
  --self-check)
    python3 -c 'import Cryptodome, gmpy2, sympy, z3'
    printf 'component-admission\n'; exit 0 ;;
esac
capability=${1:-}; input=${2:-}; test -n "$input"
python3 - "$capability" "$input" <<'PY'
import json
import pathlib
import sys

capability, supplied = sys.argv[1:]
root = pathlib.Path(supplied)
paths = sorted(root.glob("*.json")) if root.is_dir() else [root]
if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
    raise ValueError("invalid request path")

if capability == "crypto.primitive":
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.number import inverse

    print("schema=resident.crypto.primitive.v1")
    for path in paths:
        request = json.loads(path.read_text())
        operation = request.get("operation")
        if operation == "aes-ecb-decrypt" and set(request) == {"operation", "key", "ciphertext"}:
            key, ciphertext = bytes.fromhex(request["key"]), bytes.fromhex(request["ciphertext"])
            if len(key) not in {16, 24, 32} or not ciphertext or len(ciphertext) > 4096 or len(ciphertext) % 16:
                raise ValueError("invalid AES request")
            print(f"aes_plaintext={AES.new(key, AES.MODE_ECB).decrypt(ciphertext).hex()}")
        elif operation == "rsa-decrypt" and set(request) == {
            "operation", "p", "q", "public_exponent", "ciphertext"
        }:
            p, q, exponent, ciphertext = (request[name] for name in ("p", "q", "public_exponent", "ciphertext"))
            if any(not isinstance(value, int) or value <= 1 or value.bit_length() > 4096 for value in (p, q, exponent)):
                raise ValueError("invalid RSA request")
            private = inverse(exponent, (p - 1) * (q - 1))
            print(f"rsa_plaintext={pow(ciphertext, private, p * q)}")
        else:
            raise ValueError("unsupported primitive request")
elif capability == "math.symbolic":
    import gmpy2
    from sympy.ntheory.modular import crt

    if len(paths) != 1:
        raise ValueError("one symbolic request required")
    request = json.loads(paths[0].read_text())
    if set(request) != {"operation", "residues", "moduli", "inverse"} or request["operation"] != "crt-and-inverse":
        raise ValueError("unsupported symbolic request")
    residues, moduli, inverse_request = request["residues"], request["moduli"], request["inverse"]
    if not isinstance(residues, list) or not isinstance(moduli, list) or not 1 <= len(residues) == len(moduli) <= 64:
        raise ValueError("invalid CRT request")
    answer, modulus = crt(moduli, residues)
    inverse_answer = gmpy2.invert(inverse_request["value"], inverse_request["modulus"])
    print("schema=resident.math.symbolic.v1")
    print(f"crt={int(answer)} modulus={int(modulus)} inverse={int(inverse_answer)}")
elif capability == "solver.smt":
    import z3

    if len(paths) != 1:
        raise ValueError("one SMT request required")
    request = json.loads(paths[0].read_text())
    if set(request) != {"operation", "bits", "add", "target"} or request["operation"] != "bitvec-add-equals":
        raise ValueError("unsupported SMT request")
    bits, add, target = request["bits"], request["add"], request["target"]
    if not isinstance(bits, int) or not 1 <= bits <= 4096 or any(not isinstance(value, int) for value in (add, target)):
        raise ValueError("invalid SMT request")
    value = z3.BitVec("value", bits); solver = z3.Solver(); solver.add(value + add == target)
    if solver.check() != z3.sat:
        raise ValueError("SMT request is unsatisfiable")
    print("schema=resident.solver.smt.v1")
    print(f"value={solver.model()[value].as_long()}")
else:
    raise ValueError("unsupported capability")
PY
