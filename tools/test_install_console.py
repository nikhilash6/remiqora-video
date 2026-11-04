"""The installer's console look, without a download or a GPU: tools/install_models.py --plan in the three outputs (JSON for
the wizard, the person's view, plain text), the banner, the PowerShell scripts parse. Run: python tools/test_install_console.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failed = 0


def ok(cond: bool, what: str) -> None:
    global failed
    print(("  ok   " if cond else "  FAIL ") + what)
    failed += 0 if cond else 1


def run(*extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    e.pop("NO_COLOR", None) if not (env and "NO_COLOR" in env) else None
    return subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "install_models.py"), "--models-dir", d, *extra],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, env=e)


with tempfile.TemporaryDirectory() as d:
    print("--json (the wizard's parsing)")
    r = run("--sets", "writer,ltx23", "--json", "--plan")
    events = [json.loads(line) for line in r.stdout.splitlines()]
    plan = [e for e in events if e["event"] == "plan"]
    ok(r.returncode == 0 and len(plan) == 1 and set(plan[0]["sets"]) == {"writer", "ltx23"} and plan[0]["total"] > 0, "one plan event with sets and total")
    ok("\x1b" not in r.stdout and "▍" not in r.stdout, "no colour and no table in JSON")
    r = run("--sets", "writer", "--json", "--plan", "--pretty")
    ok(all(line.startswith("{") for line in r.stdout.splitlines()), "--json wins over --pretty")

    print("plain text (redirected, no flag)")
    r = run("--sets", "writer", "--plan")
    ok(r.returncode == 0 and "\x1b" not in r.stdout and "ГБ" in r.stdout and "▍" not in r.stdout, "the old one-line plan, no colour")

    print("--pretty / --dry-run")
    r = run("--profile", "minimum", "--dry-run", "--pretty", "--lang", "ru")
    ok(r.returncode == 0 and "REMIQORA VIDEO" in r.stdout and "Будет скачано" in r.stdout and "свободно" in r.stdout, "banner, sizes, free space")
    ok("\x1b" not in r.stdout, "redirected: plain")
    ok(not any(Path(d).rglob("*.part")) and not any(p.is_file() for p in Path(d).rglob("*")), "nothing downloaded")
    r = run("--profile", "minimum", "--plan", "--pretty", "--lang", "en", "--no-banner")
    ok("REMIQORA" not in r.stdout and "To download" in r.stdout, "--no-banner, English")
    r = run("--profile", "minimum", "--plan", "--pretty", env={"FORCE_COLOR": "1"})
    ok("\x1b[38;2;147;51;234m" in r.stdout and "\x1b[38;2;" in r.stdout, "FORCE_COLOR: the gradient starts at #9333ea")
    r = run("--profile", "minimum", "--plan", "--pretty", env={"FORCE_COLOR": "1", "NO_COLOR": "1"})
    ok("\x1b" not in r.stdout, "NO_COLOR wins")
    r = run("--sets", "h3", "--pretty", "--plan")
    ok(r.returncode == 1 and "H3" in r.stdout, "H3 without --accept-h3: refused in the person's view too")

print("PowerShell scripts parse (5.1)")
ps = ("$bad = 0; foreach ($f in 'setup_models.ps1','setup_prereqs.ps1','tools\\install_console.ps1') { $e = $null; $t = $null; "
      "[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path '" + str(ROOT) + "' $f), [ref]$t, [ref]$e); "
      "if ($e.Count) { $bad++; $e | ForEach-Object { Write-Output ($f + ': ' + $_.Message) } } }; exit $bad")
if os.name == "nt":
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120)
    ok(r.returncode == 0, "no parse errors" + (": " + r.stdout.strip() if r.returncode else ""))
for f in ("setup_models.ps1", "setup_prereqs.ps1", "tools/install_console.ps1"):
    raw = (ROOT / f).read_bytes()
    ok(raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), f + ": UTF-8 with BOM, CRLF")

print("FAILED" if failed else "all passed")
sys.exit(1 if failed else 0)
