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
from pathlib import Path

_NANVIX_DIR = Path(__file__).resolve().parent
_VENV = _NANVIX_DIR / "venv"
_VENV_PYTHON = _VENV / ("Scripts" if os.name == "nt" else "bin") / "python"
_ZUTIL_VERSION = "0.1.0"
# TODO: Replace with actual GitHub release URL once available.
_ZUTIL_RELEASE_URL = ""

if not sys.prefix.startswith(str(_VENV)):
    if not _VENV.exists():
        print("bootstrap: creating venv …", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])
        print("bootstrap: installing nanvix-zutil …", flush=True)
        local_path = os.environ.get("NANVIX_ZUTIL_PATH")
        if local_path:
            subprocess.check_call(
                [str(_VENV_PYTHON), "-m", "pip", "install", "-q", "-e", local_path]
            )
        elif _ZUTIL_RELEASE_URL:
            subprocess.check_call(
                [str(_VENV_PYTHON), "-m", "pip", "install", "-q", _ZUTIL_RELEASE_URL]
            )
        else:
            print(
                "error: NANVIX_ZUTIL_PATH not set and release URL is not configured.",
                file=sys.stderr,
            )
            sys.exit(3)
    rc = subprocess.call(
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]
    )
    sys.exit(rc)

# ── Build script ──────────────────────────────────────────────────────
from nanvix_zutil import Sysroot, ZScript  # noqa: E402


class Bzip2Build(ZScript):
    """Build script for nanvix/bzip2."""

    # bzip2 is a leaf library with no Nanvix library dependencies.
    # If dependencies were needed they'd be declared as a DEPS list and
    # installed via Buildroot.install_dep() in setup().

    def _make(self, *targets: str, extra_vars: dict[str, str] | None = None) -> None:
        """Run ``make -f Makefile.nanvix`` with standard Nanvix variables."""
        self.config.load()
        nanvix_sysroot = self.config.get("NANVIX_SYSROOT", "")
        if not nanvix_sysroot:
            from nanvix_zutil import log

            log.fatal(
                "NANVIX_SYSROOT is not set.",
                code=3,
                hint="Run `./z setup` first to download the sysroot.",
            )

        cmd: list[str] = [
            "make",
            "-f",
            "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={nanvix_sysroot}",
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
            gh_token=self.config.get("GH_TOKEN"),
        )
        sysroot.verify(["lib/libposix.a"])
        self.config.set("NANVIX_SYSROOT", str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Build libbz2.a static library."""
        self._make("all")

    def test(self) -> None:
        """Run smoke, integration, and functional tests."""
        platform_vars = {
            "PLATFORM": self.config.machine,
            "PROCESS_MODE": self.config.deployment_mode,
            "MEMORY_SIZE": self.config.memory_size,
        }
        self._make("test", extra_vars=platform_vars)

    def release(self) -> None:
        """Package the release tarball and verify it."""
        platform_vars = {
            "PLATFORM": self.config.machine,
            "PROCESS_MODE": self.config.deployment_mode,
            "MEMORY_SIZE": self.config.memory_size,
        }
        self._make("package", extra_vars=platform_vars)
        self._make("verify-package", extra_vars=platform_vars)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)


if __name__ == "__main__":
    Bzip2Build.main()
