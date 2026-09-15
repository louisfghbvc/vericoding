#!/usr/bin/env python3
"""
compiler.py - Compiles verified Dafny code to production target languages.

Supported target languages:
- py (Python)
- go (Go)
- js (JavaScript)
- cs (C#)
"""

import os
import sys
import shutil
import argparse
import subprocess
from typing import Dict, Any, Optional


# Two maps, because conflating them is what broke this.
#
# The previous single dict held both display names ("py": "Python") and alias
# resolutions ("python": "py") as values, and the normalization built on top of
# it was a no-op: every alias reached Dafny unchanged. Dafny rejects all four
# of them --
#
#   $ dafny build --target:python tiny.dfy
#   *** Error: No compiler found for target "python"; expecting one of 'cs' ...
#   exit 1
#
# -- so half the documented spellings of --target simply did not work.

# Canonical Dafny target -> human-readable name.
TARGET_NAMES = {
    "py": "Python",
    "go": "Go",
    "js": "JavaScript",
    "cs": "C#",
}

# Everything a user may type -> the canonical target Dafny understands.
TARGET_ALIASES = {
    "py": "py",
    "python": "py",
    "go": "go",
    "golang": "go",
    "js": "js",
    "javascript": "js",
    "cs": "cs",
    "csharp": "cs",
    "c#": "cs",
}

# Kept as a module-level name because callers and tests import it.
SUPPORTED_TARGETS = TARGET_ALIASES

# Dafny verifies these targets but cannot finish the build without a further
# toolchain that this repo neither ships nor checks for. Measured on a clean
# host with Dafny and Z3 present and nothing else:
#
#   py   exit 0
#   go   exit 3   Unable to start goimports
#   js   exit 3   node:internal/modules/cjs/loader
#   cs   exit 3   Failed to compile C# source code using 'dotnet build'
#
# Naming the requirement turns a raw third-party stack trace into an
# actionable message.
#
# These name what the TARGET needs, which is not the same as what is missing
# on any given host -- on the host these were measured, Node.js was present
# and the JavaScript build still failed, on `bignumber.js`. Reporting "needs
# Node.js" there would have been false. The probe's own error text says what
# actually went wrong; this says what the target requires in general.
TARGET_REQUIREMENTS = {
    "py": None,
    "go": "a Go toolchain including `goimports` (go install golang.org/x/tools/cmd/goimports@latest)",
    "js": "Node.js plus the `bignumber.js` package (npm install bignumber.js)",
    "cs": "the .NET SDK (`dotnet build`)",
}


class DafnyCompiler:
    def __init__(self, dafny_path: Optional[str] = None, solver_path: Optional[str] = None):
        self.dafny_bin = dafny_path or shutil.which("dafny")
        self.solver_path = solver_path or os.environ.get("VERICODING_Z3")

    def compile(self, file_path: str, target: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
        if not self.dafny_bin or not os.path.exists(self.dafny_bin):
            return {
                "success": False,
                "error": "Dafny CLI not found. Please install Dafny via `brew install dafny`."
            }

        target_key = target.strip().lower()
        if target_key not in TARGET_ALIASES:
            return {
                "success": False,
                "error": "Unsupported target language '{}'. Choose from: {}.".format(
                    target, ", ".join(sorted(set(TARGET_ALIASES)))
                ),
            }

        normalized_target = TARGET_ALIASES[target_key]

        out_path = output_dir or os.path.splitext(file_path)[0] + f"_{normalized_target}"
        os.makedirs(out_path, exist_ok=True)

        cmd = [
            self.dafny_bin,
            "build",
            f"--target:{normalized_target}",
            f"--output:{os.path.join(out_path, 'output')}",
        ]
        # `dafny build` verifies before it compiles, so it needs a solver for
        # the same reason `dafny verify` does -- and its bundled Z3 does not
        # run on every host.
        if self.solver_path:
            cmd.append(f"--solver-path={self.solver_path}")
        cmd.append(file_path)

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60
            )
            raw_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode == 0:
                return {
                    "success": True,
                    "target": normalized_target,
                    "output_dir": out_path,
                    "raw_output": raw_out.strip()
                }
            else:
                # A raw goimports or dotnet stack trace does not tell the
                # reader that the missing piece is a toolchain this repo never
                # said it needed. Name it.
                requirement = TARGET_REQUIREMENTS.get(normalized_target)
                error = f"exit code {proc.returncode}"
                if requirement:
                    error += (
                        f". Compiling to {TARGET_NAMES[normalized_target]} additionally "
                        f"requires {requirement}; verification itself does not."
                    )
                return {
                    "success": False,
                    "target": normalized_target,
                    "error": error,
                    "raw_output": raw_out.strip()
                }
        except Exception as ex:
            return {
                "success": False,
                "target": normalized_target,
                "error": str(ex)
            }


def main():
    parser = argparse.ArgumentParser(description="Compile verified Dafny code to target languages")
    parser.add_argument("file_path", help="Path to .dfy file")
    parser.add_argument("--target", default="py",
                        help="Target language: " + ", ".join(sorted(set(TARGET_ALIASES))))
    parser.add_argument("--out", default=None, help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"Error: file not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    compiler = DafnyCompiler()
    res = compiler.compile(args.file_path, target=args.target, output_dir=args.out)

    if res["success"]:
        print(f"✓ Successfully compiled {args.file_path} to {TARGET_NAMES[res['target']]}")
        print(f"  Output Directory: {res['output_dir']}")
    else:
        print(f"✗ Compilation failed: {res.get('error')}", file=sys.stderr)
        if res.get("raw_output"):
            print(f"Details:\n{res['raw_output']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
