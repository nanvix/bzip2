# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for bzip2.

Usage (from repository root):
    ./z setup      # Download sysroot
    ./z build      # Build libbz2.a
    ./z test       # Run smoke, integration, and functional tests
    ./z release    # Package release tarball
    ./z clean      # Remove build artifacts
"""

# ── Self-bootstrap preamble (stdlib only) ─────────────────────────────
# Creates .nanvix/venv, installs nanvix-zutil, and re-execs under the
# venv interpreter.  Set NANVIX_ZUTIL_PATH to a local checkout of
# nanvix/zutils for editable development (pip install -e).

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_NANVIX_DIR = Path(__file__).resolve().parent
_VENV = _NANVIX_DIR / "venv"
_VENV_PYTHON = _VENV / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
_ZUTIL_TAG = "v0.1.0-rc2"
_ZUTIL_RELEASE_BASE = "https://github.com/nanvix/zutils/releases/download"
_ZUTIL_HASH = "sha256:728a6ac6c9265ce58727569156c21877f94dbf6b449849a28585ccc6cde1b91f"


def _inside_venv() -> bool:
    """Return True if already running inside the project venv."""
    return sys.prefix != sys.base_prefix


def _create_venv() -> None:
    """Create the venv and install nanvix-zutil."""
    print("bootstrap: creating venv …", flush=True)
    subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])
    local_path = os.environ.get("NANVIX_ZUTIL_PATH")
    if local_path:
        print("bootstrap: installing nanvix-zutil (editable) …", flush=True)
        subprocess.check_call(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-q", "-e", local_path]
        )
    else:
        version = _ZUTIL_TAG.lstrip("v").replace("-", "")
        whl_name = f"nanvix_zutil-{version}-py3-none-any.whl"
        whl_url = f"{_ZUTIL_RELEASE_BASE}/{_ZUTIL_TAG}/{whl_name}"
        req_line = f"nanvix_zutil @ {whl_url} --hash={_ZUTIL_HASH}"
        print(f"bootstrap: installing nanvix-zutil ({_ZUTIL_TAG}) …", flush=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(req_line + "\n")
            req_path = f.name
        try:
            subprocess.check_call(
                [str(_VENV_PYTHON), "-m", "pip", "install", "-q",
                 "--require-hashes", "-r", req_path]
            )
        finally:
            Path(req_path).unlink(missing_ok=True)


if not _inside_venv():
    if not _VENV_PYTHON.exists():
        _create_venv()
    rc = subprocess.call(
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    sys.exit(rc)

# ── Build script ──────────────────────────────────────────────────────
from nanvix_zutil import Sysroot, ZScript, log  # noqa: E402

# Make variable names passed to Makefile.nanvix.
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"

# Config keys.
_CFG_SYSROOT = "NANVIX_SYSROOT"
_CFG_GH_TOKEN = "GH_TOKEN"

# Sysroot verification files.
_SYSROOT_REQUIRED_FILES = ["lib/libposix.a"]


class Bzip2Build(ZScript):
    """Build script for nanvix/bzip2."""

    # bzip2 is a leaf library with no Nanvix library dependencies.

    def _make(self, *targets: str, extra_vars: dict[str, str] | None = None) -> None:
        """Run ``make -f Makefile.nanvix`` with standard Nanvix variables."""
        self.config.load()
        nanvix_sysroot = self.config.get(_CFG_SYSROOT, "")
        if not nanvix_sysroot:
            log.fatal(
                f"{_CFG_SYSROOT} is not set.",
                code=3,
                hint="Run `./z setup` first to download the sysroot.",
            )

        cmd: list[str] = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={nanvix_sysroot}",
        ]
        if extra_vars:
            for key, val in extra_vars.items():
                cmd.append(f"{key}={val}")
        cmd.extend(targets)
        self.run(*cmd, cwd=self.repo_root)

    # ── Lifecycle hooks ───────────────────────────────────────────────

    def setup(self) -> None:
        """Download the Nanvix sysroot and persist its path."""
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag="latest",
            gh_token=self.config.get(_CFG_GH_TOKEN),
        )
        sysroot.verify(_SYSROOT_REQUIRED_FILES)
        self.config.set(_CFG_SYSROOT, str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Build libbz2.a static library."""
        self._make("all")

    def test(self) -> None:
        """Run smoke, integration, and functional tests."""
        platform_vars = {
            _MAKE_VAR_PLATFORM: self.config.machine,
            _MAKE_VAR_PROCESS_MODE: self.config.deployment_mode,
            _MAKE_VAR_MEMORY_SIZE: self.config.memory_size,
        }
        self._make("test", extra_vars=platform_vars)

    def release(self) -> None:
        """Package the release tarball and verify it."""
        platform_vars = {
            _MAKE_VAR_PLATFORM: self.config.machine,
            _MAKE_VAR_PROCESS_MODE: self.config.deployment_mode,
            _MAKE_VAR_MEMORY_SIZE: self.config.memory_size,
        }
        self._make("package", extra_vars=platform_vars)
        self._make("verify-package", extra_vars=platform_vars)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)


if __name__ == "__main__":
    Bzip2Build.main()
