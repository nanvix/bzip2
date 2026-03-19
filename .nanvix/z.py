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
import sysconfig
from pathlib import Path

_NANVIX_DIR = Path(__file__).resolve().parent
_VENV = _NANVIX_DIR / "venv"
_VENV_SCRIPTS = Path(
    sysconfig.get_path("scripts", vars={"base": str(_VENV), "platbase": str(_VENV)})
)
_VENV_PYTHON = _VENV_SCRIPTS / ("python.exe" if os.name == "nt" else "python")
_ZUTIL_TAG = "v0.1.0-rc1"


def _inside_venv() -> bool:
    """Return True if already running inside the project venv."""
    if sys.prefix == sys.base_prefix:
        return False
    try:
        return Path(sys.executable).resolve().is_relative_to(_VENV.resolve())
    except (OSError, ValueError):
        return False


def _zutil_urls() -> tuple[str, str, str]:
    """Derive wheel URL, checksums URL, and wheel filename from the pinned tag."""
    version = _ZUTIL_TAG.lstrip("v").replace("-", "")
    whl_name = f"nanvix_zutil-{version}-py3-none-any.whl"
    base = f"https://github.com/nanvix/zutils/releases/download/{_ZUTIL_TAG}"
    return f"{base}/{whl_name}", f"{base}/checksums.sha256", whl_name


def _verify_and_install_wheel() -> None:
    """Download the nanvix-zutil wheel, verify its hash, and install it."""
    import hashlib
    import tempfile
    import urllib.error
    import urllib.request

    whl_url, checksums_url, whl_name = _zutil_urls()

    with tempfile.TemporaryDirectory() as tmpdir:
        whl_path = Path(tmpdir) / whl_name
        print(f"bootstrap: downloading nanvix-zutil ({_ZUTIL_TAG}) …", flush=True)
        urllib.request.urlretrieve(whl_url, whl_path)

        expected_hash: str | None = None
        try:
            with urllib.request.urlopen(checksums_url) as resp:
                for line in resp.read().decode().splitlines():
                    if whl_name in line:
                        expected_hash = line.split()[0]
                        break
        except urllib.error.URLError:
            print(
                "warning: could not fetch checksums, skipping verification",
                file=sys.stderr,
                flush=True,
            )

        if expected_hash:
            actual = hashlib.sha256(whl_path.read_bytes()).hexdigest()
            if actual != expected_hash:
                print(
                    f"error: hash mismatch for nanvix-zutil wheel\n"
                    f"  expected: {expected_hash}\n"
                    f"  actual:   {actual}",
                    file=sys.stderr,
                )
                sys.exit(1)

        subprocess.check_call(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-q", str(whl_path)]
        )


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
        _verify_and_install_wheel()


if not _inside_venv():
    if not _VENV_PYTHON.exists():
        _create_venv()
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
