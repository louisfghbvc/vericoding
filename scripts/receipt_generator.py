#!/usr/bin/env python3
"""
receipt_generator.py - Generates verifiable cryptographic receipts and SMT proof artifacts.

Creates standard-format audit artifacts:
1. Portable SMT-LIB2 benchmark / proof output
2. Cryptographic JSON Verification Receipt with SHA-256 digests
3. Offline verification / audit replay capability
"""

import os
import sys
import json
import time
import hashlib
import argparse
import subprocess
from typing import Dict, Any, Optional

try:
    from verify_loop import DafnyVerifier
    from check_env import run_diagnostics
except ImportError:
    from scripts.verify_loop import DafnyVerifier
    from scripts.check_env import run_diagnostics


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_file_sha256(file_path: str) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class ReceiptGenerator:
    def __init__(self, dafny_path: Optional[str] = None):
        self.verifier = DafnyVerifier(dafny_path)

    def generate(
        self,
        file_path: str,
        nl_intent: Optional[str] = None,
        output_receipt_path: Optional[str] = None,
        dump_smt: bool = True
    ) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            code_content = f.read()

        file_hash = compute_sha256(code_content)
        intent_text = nl_intent or "Unspecified intent (direct code verification)"
        intent_hash = compute_sha256(intent_text)

        # 1. Run formal verification
        start_time = time.time()
        verification_result = self.verifier.verify(file_path)
        elapsed_sec = round(time.time() - start_time, 3)

        env_diag = run_diagnostics()

        # 2. SMT Proof Dump
        artifact = self._emit_proof_artifact(file_path) if dump_smt else {
            "path": None, "sha256": None, "replayable": False,
            "reason": "proof dump disabled by caller",
        }
        smt_artifact_path = artifact["path"]
        smt_hash = artifact["sha256"]

        receipt = {
            "schema_version": "vericoding.proof.v1",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_file": os.path.abspath(file_path),
            "hashes": {
                "natural_language_intent_sha256": intent_hash,
                "dafny_source_sha256": file_hash,
                "smt2_proof_sha256": smt_hash,
            },
            "formal_verification": {
                "verified": verification_result.get("verified", False),
                "duration_seconds": elapsed_sec,
                "solver_backend": "Z3 / SMT-LIB2",
                "errors_count": len(verification_result.get("diagnostics", [])),
                "diagnostics": verification_result.get("diagnostics", []),
            },
            "environment": {
                "dafny_version": env_diag["dafny"]["version"],
                "z3_version": env_diag["z3_python"]["version"] or env_diag["z3_cli"]["version"],
                "platform": sys.platform,
            },
            "proof_artifact": {
                "smt2_file": smt_artifact_path,
                # Never assert replayability we have not demonstrated. An
                # artifact nobody has re-run is a claim, not a proof.
                "replayable": artifact["replayable"],
                "reason": artifact.get("reason"),
                "assertion_count": artifact.get("assertion_count"),
                "audit_command": (
                    # Replay checks the ARTIFACT. `dafny verify` re-runs the
                    # proof from source, which tells the auditor nothing about
                    # whether the archived file contains it.
                    f"z3 {os.path.basename(smt_artifact_path)}"
                    if smt_artifact_path else None
                ),
            }
        }

        # Calculate self-receipt hash (seal)
        receipt_seal = compute_sha256(json.dumps(receipt, sort_keys=True))
        receipt["receipt_seal_sha256"] = receipt_seal

        target_receipt_path = output_receipt_path or f"{os.path.splitext(file_path)[0]}.receipt.json"
        with open(target_receipt_path, "w", encoding="utf-8") as rf:
            json.dump(receipt, rf, indent=2)

        return receipt

    # ------------------------------------------------------------------
    # Proof artifact emission
    # ------------------------------------------------------------------
    #
    # Getting a real SMT-LIB2 log out of Dafny is harder than it looks, and
    # every failure mode here is silent:
    #
    #   * `--solver-option:smt.dump_models=true` dumps counterexample MODELS,
    #     not the proof log. It writes no .smt2 file at all.
    #   * The modern `--boogie /proverLog:F` spelling, and its `=` and quoted
    #     variants, all report "N verified, 0 errors" and create ZERO files.
    #   * Only the deprecated legacy CLI actually writes the log. It prints
    #     "Warning: this way of using the CLI is deprecated." and works.
    #
    # Because all three of those fail quietly, the previous implementation
    # fell back to WRITING A STUB -- a five-line file containing
    # `(set-logic ALL)` and `(check-sat)` and no assertions whatsoever -- then
    # hashed it and recorded `replayable: true`. That file re-checks as `sat`
    # because an empty query is trivially satisfiable. The receipt
    # cryptographically attested to a file containing no proof.
    #
    # So: emit for real, validate what came out, and when there is no artifact
    # say so. A missing artifact is a fact worth recording. A fabricated one
    # is worse than nothing, because it carries authority it has not earned.

    MIN_ARTIFACT_BYTES = 1024
    VALID_PREFIXES = ("(set-option", "(set-logic", "(set-info", ";")

    def _emit_proof_artifact(self, file_path: str) -> Dict[str, Any]:
        base_name = os.path.splitext(file_path)[0]
        out_path = f"{base_name}.smt2"
        none = {"path": None, "sha256": None, "replayable": False,
                "assertion_count": 0}

        if not self.verifier.is_available():
            return dict(none, reason="dafny not available; no proof log emitted")

        # Remove any previous artifact so a stale file can never be mistaken
        # for output of this run.
        if os.path.exists(out_path):
            os.remove(out_path)

        cmd = [self.verifier.dafny_bin, "/compile:0"]
        if self.verifier.solver_path:
            cmd.append(f"/proverOpt:PROVER_PATH={self.verifier.solver_path}")
        cmd += [f"/proverLog:{out_path}", file_path]

        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120)
        except Exception as ex:
            return dict(none, reason=f"proof log emission failed: {ex}")

        if not os.path.exists(out_path):
            return dict(none, reason=(
                "dafny produced no proof log. The modern --boogie /proverLog: "
                "spellings silently write nothing; only the legacy CLI emits one."
            ))

        content = ""
        try:
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as ex:
            return dict(none, reason=f"proof log unreadable: {ex}")

        size = len(content.encode("utf-8"))
        assertions = content.count("(assert")
        stripped = content.lstrip()

        # A real log carries the solver's actual obligations. These three
        # checks are cheap and each one independently rejects the stub the
        # previous implementation used to write.
        problems = []
        if size < self.MIN_ARTIFACT_BYTES:
            problems.append(f"only {size} bytes (a real log is tens of KB)")
        if assertions == 0:
            problems.append("contains no (assert ...) terms, so it proves nothing")
        if not stripped.startswith(self.VALID_PREFIXES):
            problems.append("does not begin with an SMT-LIB2 preamble")

        if problems:
            os.remove(out_path)
            return dict(none, reason=(
                "emitted file rejected and deleted: " + "; ".join(problems)
            ))

        return {
            "path": out_path,
            "sha256": compute_file_sha256(out_path),
            "replayable": True,
            "assertion_count": assertions,
            "reason": None,
        }


def recompute_seal(data: Dict[str, Any]) -> str:
    """Recompute a receipt's seal exactly as `generate` produced it.

    The seal covers the receipt with the seal key itself removed, so this must
    mirror generate()'s ordering or every receipt will look tampered with.
    """
    body = {k: v for k, v in data.items() if k != "receipt_seal_sha256"}
    return compute_sha256(json.dumps(body, sort_keys=True))


def verify_receipt(receipt_file: str) -> bool:
    """Check that a receipt still describes the code and the proof beside it.

    Every line this prints corresponds to something that was checked. That is
    not a stylistic preference -- the previous version printed three green
    checkmarks while validating exactly one thing:

      * "Receipt Seal: <hash>" printed the stored seal without recomputing it,
        so replacing it with 64 zeros produced a green line and exit 0.
      * The proof artifact was never examined, so corrupting
        hashes.smt2_proof_sha256 also passed.

    A receipt is a claim about three artifacts -- the source, the proof, and
    itself. Checking one of them and reporting on three is the same defect the
    stub proof artifact was: authority that has not been earned.
    """
    with open(receipt_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    checks, failures = [], []

    # 1. The receipt has not been edited since it was issued.
    stored_seal = data.get("receipt_seal_sha256")
    if not stored_seal:
        failures.append("receipt carries no seal")
    else:
        actual_seal = recompute_seal(data)
        if actual_seal != stored_seal:
            failures.append(
                "receipt seal mismatch -- the receipt was edited after issuance\n"
                "    recorded: {}\n    actual  : {}".format(stored_seal, actual_seal)
            )
        else:
            checks.append("Receipt seal intact ({}...)".format(stored_seal[:16]))

    # 2. The source still hashes to what was verified.
    source_file = data.get("source_file")
    expected_source = data.get("hashes", {}).get("dafny_source_sha256")
    if not source_file or not os.path.exists(source_file):
        failures.append("source file not found: {}".format(source_file))
    else:
        current = compute_file_sha256(source_file)
        if current != expected_source:
            failures.append(
                "source changed since issuance\n"
                "    recorded: {}\n    actual  : {}".format(expected_source, current)
            )
        else:
            checks.append("Source matches ({}...)".format(current[:12]))

    # 3. The proof artifact, if the receipt claims one, is the one it claims.
    artifact = data.get("proof_artifact") or {}
    smt2_path = artifact.get("smt2_file")
    recorded_proof = data.get("hashes", {}).get("smt2_proof_sha256")
    if not smt2_path:
        # Not a failure. A receipt that honestly records having no proof is
        # still a valid receipt -- it just cannot claim a verified artifact.
        checks.append(
            "No proof artifact claimed ({})".format(
                artifact.get("reason") or "reason not recorded"
            )
        )
    elif not os.path.exists(smt2_path):
        failures.append("receipt names a proof artifact that is missing: {}".format(smt2_path))
    else:
        actual_proof = compute_file_sha256(smt2_path)
        if actual_proof != recorded_proof:
            failures.append(
                "proof artifact changed since issuance: {}\n"
                "    recorded: {}\n    actual  : {}".format(
                    smt2_path, recorded_proof, actual_proof)
            )
        else:
            checks.append("Proof artifact matches ({}...)".format(actual_proof[:12]))

    for line in checks:
        print("✓ {}".format(line))
    for line in failures:
        print("✗ {}".format(line))

    # Reported last so it cannot be mistaken for a check. It is a record of what
    # the verifier said at issuance, not something this command re-establishes.
    status = data.get("formal_verification", {}).get("verified")
    print("  status at issuance: {} (recorded, not re-verified here)".format(
        "VERIFIED" if status else "NOT VERIFIED"))

    return not failures


def main():
    parser = argparse.ArgumentParser(description="Vericoding Cryptographic Receipt Generator & Auditor")
    subparsers = parser.add_subparsers(dest="action", required=True)

    gen_parser = subparsers.add_parser("create", help="Create a proof receipt for a .dfy file")
    gen_parser.add_argument("file_path", help="Path to .dfy file")
    gen_parser.add_argument("--intent", default=None, help="Natural language requirements text")
    gen_parser.add_argument("--out", default=None, help="Output path for receipt JSON")

    check_parser = subparsers.add_parser("audit", help="Audit an existing receipt against local source")
    check_parser.add_argument("receipt_path", help="Path to .receipt.json file")

    args = parser.parse_args()

    if args.action == "create":
        rg = ReceiptGenerator()
        res = rg.generate(args.file_path, nl_intent=args.intent, output_receipt_path=args.out)
        # Print the path that was actually written. This used to append
        # ".receipt.json" to the full source path including its extension,
        # producing "foo.dfy.receipt.json" while generate() wrote
        # "foo.receipt.json" -- so following the printed path hit a
        # file-not-found on an artifact that existed.
        written = args.out or f"{os.path.splitext(args.file_path)[0]}.receipt.json"
        print(f"✓ Receipt generated: {written}")
        print(f"  Verified    : {res['formal_verification']['verified']}")

        artifact = res["proof_artifact"]
        if artifact["replayable"]:
            print(f"  Proof       : {artifact['smt2_file']} "
                  f"({artifact['assertion_count']} assertions)")
            print(f"  Replay with : {artifact['audit_command']}")
        else:
            # Say it plainly. A receipt with no proof behind it is still a
            # useful record, but only if it does not read like one that has.
            print(f"  Proof       : NONE — {artifact['reason']}")
        print(f"  Seal SHA256 : {res['receipt_seal_sha256']}")
    elif args.action == "audit":
        ok = verify_receipt(args.receipt_path)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
