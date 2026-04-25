# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for bzip2.

Usage:
    ./z setup                  # Download Nanvix sysroot
    ./z build                  # Cross-compile (Linux, or Docker container)
    ./z build --with-docker    # Cross-compile via Docker (required on Windows)
    ./z test                   # Run test suite
    ./z release                # Package release tarball
    ./z clean                  # Remove build artifacts
"""

import dataclasses
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    EXIT_MISSING_DEP,
    DockerConfig,
    ZScript,
    is_windows,
    log,
)

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"

# Build output files that must be copied back from the Docker container
# to the host workspace after a Windows Docker build.
_BUILD_OUTPUT_FILES = [
    "libbz2.a",
    "bzip2.elf",
]


class Bzip2Build(ZScript):
    """Build script for nanvix/bzip2."""

    def _get_sysroot(self) -> str:
        """Return the sysroot path, or fatal if not set."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        return sysroot

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        sysroot = self._get_sysroot()
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix")
        sysroot_p = self.translate_path(Path(sysroot))

        # The toolchain path lives inside the Docker container
        # (/opt/nanvix) and is not a host path. Avoid passing it through
        # Path() on Windows which would mangle the forward slashes.
        if self.docker is not None:
            toolchain_p = toolchain
        else:
            toolchain_p = self.translate_path(Path(toolchain))

        args = [
            "make", "-f", "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
            f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
            f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
            f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
        ]
        args.extend(targets)
        return args

    # ------------------------------------------------------------------
    # Docker configuration
    # ------------------------------------------------------------------

    def docker_config(self, image: str) -> DockerConfig:
        """Build Docker configuration with bzip2 output files.

        Extends the base class configuration to specify which build
        artifacts must be copied back from the container to the host
        workspace after a Windows Docker build.
        """
        cfg = super().docker_config(image)
        return dataclasses.replace(cfg, output_files=_BUILD_OUTPUT_FILES)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Download the Nanvix sysroot."""
        super().setup()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Cross-compile libbz2.a and bzip2.elf for Nanvix.

        Uses ``self.run()`` which transparently wraps in Docker when
        ``--with-docker`` is passed. On Linux CI the build runs inside
        the toolchain container directly. On Windows, pass
        ``--with-docker`` to cross-compile via Docker Desktop.
        """
        self.run(*self._make_args("all", "bzip2.elf"), cwd=self.repo_root)

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def test(self) -> None:
        """Run the test suite.

        On Linux, delegates to the Makefile (smoke + integration + functional).
        On Windows, runs bzip2.elf through nanvixd.exe in standalone mode
        with compress/decompress tests.
        """
        if is_windows():
            self._run_tests_windows()
            return
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_tests_windows(self) -> None:
        """Run bzip2 standalone tests on Windows using nanvixd.exe.

        Expects bzip2.elf to already exist (from a prior ``./z build
        --with-docker``). Runs compress and decompress tests via
        nanvixd.exe + mkramfs.exe in standalone mode.

        Windows only supports standalone deployment mode. Attempting to
        run tests in any other mode will raise an error.
        """
        if self.config.deployment_mode != "standalone":
            raise RuntimeError(
                f"Windows tests only support standalone mode "
                f"(got: {self.config.deployment_mode}). "
                f"Single-process and multi-process modes are Linux-only."
            )
        machine = self.config.machine
        sysroot = self._get_sysroot()
        sysroot_path = Path(sysroot)

        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )

        bzip2_elf = self.repo_root / "bzip2.elf"
        if not bzip2_elf.is_file():
            log.fatal(
                "bzip2.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build --with-docker` first.",
            )

        sample_ref = self.repo_root / "tests" / "sample1.ref"
        sample_bz2 = self.repo_root / "tests" / "sample1.bz2"
        if not sample_ref.is_file() or not sample_bz2.is_file():
            log.fatal(
                "Test data not found (tests/sample1.ref or tests/sample1.bz2).",
                code=EXIT_MISSING_DEP,
            )

        bin_dir = str((sysroot_path / "bin").resolve())

        print(f"=== bzip2 {machine} standalone compress test ===")
        self._run_nanvixd_test(
            label=f"sample1 compress standalone ({machine})",
            bzip2_elf=bzip2_elf,
            test_file=sample_ref,
            test_file_guest_name="sample1.ref",
            nanvixd=nanvixd, mkramfs=mkramfs, bin_dir=bin_dir,
            bzip2_args=["-1", "-k", "-f", "/tmp/sample1.ref"],
        )

        print(f"=== bzip2 {machine} standalone decompress test ===")
        self._run_nanvixd_test(
            label=f"sample1 decompress standalone ({machine})",
            bzip2_elf=bzip2_elf,
            test_file=sample_bz2,
            test_file_guest_name="sample1.bz2",
            nanvixd=nanvixd, mkramfs=mkramfs, bin_dir=bin_dir,
            bzip2_args=["-d", "-k", "-f", "/tmp/sample1.bz2"],
        )

        print(f"=== All bzip2 Windows {machine} standalone tests PASSED ===")

    def _run_nanvixd_test(
        self,
        *,
        label: str,
        bzip2_elf: Path,
        test_file: Path,
        test_file_guest_name: str,
        nanvixd: Path,
        mkramfs: Path,
        bin_dir: str,
        bzip2_args: list[str],
    ) -> None:
        """Run a single bzip2 test inside nanvixd.exe."""
        tmpdir_path = Path(tempfile.mkdtemp(prefix="nanvix_bzip2_"))
        try:
            ramfs_dir = tmpdir_path / "ramfs"
            ramfs_dir.mkdir()
            (ramfs_dir / "tmp").mkdir()
            shutil.copy2(bzip2_elf, ramfs_dir / "bzip2.elf")
            shutil.copy2(test_file, ramfs_dir / "tmp" / test_file_guest_name)
            ramfs_img = tmpdir_path / "rootfs.img"

            subprocess.run(
                [str(mkramfs.resolve()), "-o", str(ramfs_img), str(ramfs_dir)],
                check=True, timeout=60,
            )

            cmd = [
                str(nanvixd.resolve()),
                "-bin-dir", bin_dir,
                "-ramfs", str(ramfs_img),
                "--", "./bzip2.elf", *bzip2_args,
            ]
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True,
                timeout=120,
            )
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="")
            if result.returncode != 0:
                raise RuntimeError(
                    f"{label} failed (exit code {result.returncode})"
                )
            print(f"  PASS: {label}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{label} timed out (120s)")
        finally:
            shutil.rmtree(tmpdir_path, ignore_errors=True)

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def release(self) -> None:
        """Package the bzip2 release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    # ------------------------------------------------------------------
    # Clean
    # ------------------------------------------------------------------

    def clean(self) -> None:
        """Remove build artifacts."""
        if is_windows():
            for pattern in ["*.o", "*.a", "*.elf"]:
                for f in self.repo_root.glob(pattern):
                    f.unlink()
            dist_dir = self.repo_root / "dist"
            if dist_dir.exists():
                shutil.rmtree(dist_dir)
            print("Cleaned build artifacts")
            return
        self.run(
            "make", "-f", "Makefile.nanvix", "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    Bzip2Build.main()
