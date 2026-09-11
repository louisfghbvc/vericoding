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


SUPPORTED_TARGETS = {
    "py": "Python",
    "python": "py",
    "go": "Go",
    "golang": "go",
    "js": "JavaScript",
    "javascript": "js",
    "cs": "C#",
    "csharp": "cs",
}


class DafnyCompiler:
    def __init__(self, dafny_path: Optional[str] = None):
        self.dafny_bin = dafny_path or shutil.which("dafny")

    def compile(self, file_path: str, target: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
        if not self.dafny_bin or not os.path.exists(self.dafny_bin):
            return {
                "success": False,
                "error": "Dafny CLI not found. Please install Dafny via `brew install dafny`."
            }

        target_key = target.lower()
        if target_key not in SUPPORTED_TARGETS:
            return {
                "success": False,
                "error": f"Unsupported target language '{target}'. Choose from: py, go, js, cs."
            }

        target_lang = SUPPORTED_TARGETS[target_key]
        if target_lang in SUPPORTED_TARGETS.values():
            normalized_target = target_key
        else:
            normalized_target = SUPPORTED_TARGETS[target_key]

        out_path = output_dir or os.path.splitext(file_path)[0] + f"_{normalized_target}"
        os.makedirs(out_path, exist_ok=True)

        cmd = [
            self.dafny_bin,
            "build",
            f"--target:{normalized_target}",
            f"--output:{os.path.join(out_path, 'output')}",
            file_path
        ]

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
                return {
                    "success": False,
                    "target": normalized_target,
                    "error": f"Compilation failed (exit code {proc.returncode})",
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
    parser.add_argument("--target", default="py", help="Target language (py, go, js, cs)")
    parser.add_argument("--out", default=None, help="Output directory")
    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"Error: file not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    compiler = DafnyCompiler()
    res = compiler.compile(args.file_path, target=args.target, output_dir=args.out)

    if res["success"]:
        print(f"✓ Successfully compiled {args.file_path} to {res['target'].upper()}!")
        print(f"  Output Directory: {res['output_dir']}")
    else:
        print(f"✗ Compilation failed: {res.get('error')}", file=sys.stderr)
        if res.get("raw_output"):
            print(f"Details:\n{res['raw_output']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
