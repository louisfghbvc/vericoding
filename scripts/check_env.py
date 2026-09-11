#!/usr/bin/env python3
"""
check_env.py - Check availability of Dafny, Z3, and target runtime environments.
"""
import shutil
import subprocess
import sys
from typing import Dict, Any


def check_tool(name: str, version_flag: str = "--version") -> Dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"installed": False, "path": None, "version": None}
    try:
        out = subprocess.run(
            [path, version_flag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        version_str = (out.stdout or out.stderr).strip().split("\n")[0]
        return {"installed": True, "path": path, "version": version_str}
    except Exception as e:
        return {"installed": True, "path": path, "version": f"error reading version: {e}"}


def check_python_z3() -> Dict[str, Any]:
    try:
        import z3
        return {"installed": True, "version": z3.get_version_string()}
    except ImportError:
        return {"installed": False, "version": None}


def run_diagnostics() -> Dict[str, Any]:
    tools = {
        "dafny": check_tool("dafny", "--version"),
        "z3_cli": check_tool("z3", "--version"),
        "z3_python": check_python_z3(),
        "dotnet": check_tool("dotnet", "--version"),
        "go": check_tool("go", "version"),
        "node": check_tool("node", "--version"),
        "python3": check_tool("python3", "--version"),
    }
    return tools


def print_report():
    tools = run_diagnostics()
    print("========================================")
    print("   Vericoding Environment Diagnostics   ")
    print("========================================")
    
    dafny_status = "✓ Ready" if tools["dafny"]["installed"] else "✗ Missing (Run: brew install dafny)"
    print(f"[*] Dafny CLI       : {dafny_status}")
    if tools["dafny"]["installed"]:
        print(f"    Path            : {tools['dafny']['path']}")
        print(f"    Version         : {tools['dafny']['version']}")

    z3_status = "✓ Ready" if tools["z3_python"]["installed"] else "✗ Missing (Run: pip install z3-solver)"
    print(f"[*] Python Z3 Engine: {z3_status}")
    if tools["z3_python"]["installed"]:
        print(f"    Version         : {tools['z3_python']['version']}")

    print("\nTarget Language Compilers:")
    for lang in ["python3", "go", "node"]:
        status = "✓ Ready" if tools[lang]["installed"] else "✗ Not detected"
        ver = tools[lang]["version"] or ""
        print(f"  - {lang:<10} : {status} {ver}")
    print("========================================")


if __name__ == "__main__":
    print_report()
