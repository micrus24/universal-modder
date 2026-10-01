"""Small helpers shared by the subcommands: platform checks, WSL path mapping, subprocess, output."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def is_mac() -> bool:
    return sys.platform == "darwin"


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    return "microsoft" in platform.release().lower() or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")


def to_win(path: str | Path) -> str:
    """/mnt/c/Games/x -> C:\\Games\\x (WSL); paths that are already Windows paths pass through."""
    p = str(path)
    if len(p) > 1 and p[1] == ":":
        return p
    if p.startswith("/mnt/") and len(p) > 6 and p[6] in "/" and p[5].isalpha():
        return p[5].upper() + ":\\" + p[7:].replace("/", "\\")
    if p.startswith("/mnt/") and len(p) == 6:
        return p[5].upper() + ":\\"
    if is_wsl() and shutil.which("wslpath"):
        return subprocess.run(["wslpath", "-w", p], capture_output=True, text=True).stdout.strip()
    return p


def to_posix(path: str | Path) -> str:
    """C:\\Games\\x -> /mnt/c/Games/x under WSL; unchanged elsewhere."""
    p = str(path)
    if is_wsl() and len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def data_dir(create: bool = True) -> Path:
    """Per-user state: backups, downloaded tools. Override with UM_HOME. create=False just names the folder."""
    d = Path(os.environ.get("UM_HOME", Path.home() / ".universal-modder"))
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def dotenv_value(key: str) -> str | None:
    """KEY=value from a .env file in the working folder or the toolkit's own folder (not from the environment)."""
    import re
    for env in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if env.exists():
            m = re.search(rf"^\s*{re.escape(key)}\s*=\s*['\"]?([^'\"\s]+)", env.read_text(encoding="utf-8", errors="replace"), re.M)
            if m:
                return m.group(1)
    return None


def parse_kv(pairs: list[str] | None) -> dict:
    """key=value (string) and key:=json (number/bool/list/object); a value '@file' is left for the caller to upload."""
    out = {}
    for p in pairs or []:
        if ":=" in p:
            k, v = p.split(":=", 1)
            out[k] = json.loads(v)
        elif "=" in p:
            k, v = p.split("=", 1)
            out[k] = v
        else:
            die(f"bad argument {p!r}: use key=value or key:=json")
    return out


def asset_name(args, fallback: str) -> str:
    """--name if given, else a short file stem made from the prompt."""
    import re
    if getattr(args, "name", None):
        return args.name
    words = re.sub(r"[^a-z0-9 ]", "", fallback.lower()).split()[:5]
    return "_".join(words) or "asset"


def _version_key(path: Path) -> tuple:
    """('Blender 4.2' -> (4, 2)): installed versions compare as numbers, not text ('10.0' > '5.2')."""
    import re
    m = re.search(r"(\d+(?:\.\d+)*)", path.parent.name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def _windows_drives() -> list[str]:
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [chr(65 + i) for i in range(26) if mask >> i & 1]


def blender_bin() -> str:
    """BLENDER=, else PATH, else the usual install folders (any drive on Windows, newest version first)."""
    b = os.environ.get("BLENDER") or shutil.which("blender")
    if b:
        return b
    if Path("/Applications/Blender.app/Contents/MacOS/Blender").exists():
        return "/Applications/Blender.app/Contents/MacOS/Blender"
    if os.name == "nt":   # the installer's default folder is versioned ("Blender 5.2") and may be on any drive
        roots = [Path(f"{d}:\\{pf}\\Blender Foundation") for d in _windows_drives() for pf in ("Program Files", "Program Files (x86)")]
        hits = sorted((p for r in roots for p in r.glob("Blender*/blender.exe")), key=_version_key, reverse=True)
        if hits:
            return str(hits[0])
    die("Blender not found: install it (blender.org, or `snap install blender --classic`) or set BLENDER=/path/to/blender")


def run_blender(script: Path, cfg: dict, timeout: float | None = None) -> subprocess.CompletedProcess:
    """`blender -b --python script -- cfg.json`. The config is a temp file that is removed afterwards; output is decoded
    as UTF-8 (Blender's, not the console code page)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="um_blender_") as d:
        f = Path(d) / "config.json"
        f.write_text(json.dumps(cfg), encoding="utf-8")
        return subprocess.run([blender_bin(), "-b", "--python", str(script), "--", str(f)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)


def run(cmd: list[str], check: bool = True, capture: bool = True, timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout, **kw)
    except FileNotFoundError:
        die(f"not found: {cmd[0]}")
    except subprocess.CalledProcessError as e:
        die(f"{' '.join(map(str, cmd[:3]))}... failed ({e.returncode}):\n{(e.stderr or e.stdout or '').strip()[-2000:]}")


def die(msg: str, code: int = 1):
    print(f"um: {msg}", file=sys.stderr)
    sys.exit(code)


def emit(obj, as_json: bool = False):
    if as_json or not isinstance(obj, str):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def parse_size(s: str) -> tuple[int, int]:
    """'64x26' -> (64, 26); '128' -> (128, 128)."""
    if "x" in s.lower():
        w, h = s.lower().split("x", 1)
        return int(w), int(h)
    return int(s), int(s)


def need(module: str, pip_name: str | None = None):
    try:
        return __import__(module)
    except ImportError:
        die(f"this command needs {pip_name or module}: `pip install {pip_name or module}` (or run through bin/um, which uses uv)")
