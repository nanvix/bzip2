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
            "make",
            "-f",
            "Makefile.nanvix",
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

    def setup(self) -> bool:
        """Download the Nanvix sysroot."""
        return super().setup()

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

        Smoke and integration tests are always delegated to the Makefile.
        The functional test in standalone mode is handled in Python via
        make_initrd so that initrd creation is shared across platforms.
        """
        if is_windows():
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            targets = self.targets if self.targets else []
            # Targets that require the Python functional path.
            _functional_targets = {"test", "test-functional"}
            needs_functional = not targets or bool(set(targets) & _functional_targets)
            # Delegate non-functional targets to the Makefile.
            make_targets = [t for t in targets if t not in _functional_targets]
            if not targets:
                make_targets = ["test-smoke", "test-integration"]
            elif needs_functional and not make_targets:
                # Ensure Makefile prerequisites run when only functional
                # targets are requested (build + smoke/integration).
                if "test" in targets:
                    make_targets = ["test-smoke", "test-integration"]
                else:
                    make_targets = ["test-integration"]
            if make_targets:
                self.run(*self._make_args(*make_targets), cwd=self.repo_root)
            if needs_functional:
                self._run_functional_standalone()
        else:
            targets = self.targets if self.targets else ["test"]
            self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling bzip2.elf with system daemons via
        make_initrd, and a ramfs providing /tmp with test data files.
        """
        bzip2_elf = self.repo_root / "bzip2.elf"
        if not bzip2_elf.is_file():
            log.fatal(
                "bzip2.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        sysroot = self._get_sysroot()
        sysroot_path = Path(sysroot)
        mkramfs = sysroot_path / "bin" / "mkramfs.elf"

        _compress_samples = [
            ("sample1", ".ref", "-1"),
            ("sample2", ".ref", "-2"),
            ("sample3", ".ref", "-3"),
        ]
        _decompress_samples = ["sample1", "sample2", "sample3"]

        print("=== bzip2 functional tests ===")

        for name, ext, level in _compress_samples:
            test_file = self.repo_root / "tests" / f"{name}{ext}"
            print(f"  Running bzip2 compress test ({name}, {level})...")
            initrd = self.make_initrd(
                "bzip2.elf", app_args=[level, "-k", "-f", f"/tmp/{name}{ext}"]
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}{ext}")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    self.run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        docker=False,
                    )

                    self.run(
                        str(sysroot_path / "bin" / "nanvixd.elf"),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        docker=False,
                        timeout=120,
                    )
            finally:
                if initrd.exists():
                    initrd.unlink()
            print(f"  PASS: {name} compress")

        for name in _decompress_samples:
            test_file = self.repo_root / "tests" / f"{name}.bz2"
            # Use -ds for sample3 (small-memory decompression mode).
            decompress_flag = "-ds" if name == "sample3" else "-d"
            print(f"  Running bzip2 decompress test ({name})...")
            initrd = self.make_initrd(
                "bzip2.elf",
                app_args=[decompress_flag, "-k", "-f", f"/tmp/{name}.bz2"],
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}.bz2")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    self.run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        docker=False,
                    )

                    self.run(
                        str(sysroot_path / "bin" / "nanvixd.elf"),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        docker=False,
                        timeout=120,
                    )
            finally:
                if initrd.exists():
                    initrd.unlink()
            print(f"  PASS: {name} decompress")

        print("  PASS: bzip2 functional tests")
        print("=== All bzip2 tests PASSED ===")

    def _run_tests_windows(self) -> None:
        """Run bzip2 standalone tests on Windows using nanvixd.exe.

        Expects bzip2.elf to already exist (from a prior ``./z build
        --with-docker``). Uses make_initrd to bundle the binary with
        system daemons, and a ramfs for test input/output files.

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

        _compress_samples = [
            ("sample1", ".ref", "-1"),
            ("sample2", ".ref", "-2"),
            ("sample3", ".ref", "-3"),
        ]
        _decompress_samples = ["sample1", "sample2", "sample3"]

        for name, ext, _ in _compress_samples:
            p = self.repo_root / "tests" / f"{name}{ext}"
            if not p.is_file():
                log.fatal(
                    f"Test data not found (tests/{name}{ext}).",
                    code=EXIT_MISSING_DEP,
                )
        for name in _decompress_samples:
            p = self.repo_root / "tests" / f"{name}.bz2"
            if not p.is_file():
                log.fatal(
                    f"Test data not found (tests/{name}.bz2).",
                    code=EXIT_MISSING_DEP,
                )

        failed: list[str] = []

        for name, ext, level in _compress_samples:
            test_file = self.repo_root / "tests" / f"{name}{ext}"
            print(f"=== bzip2 {machine} standalone compress test ({name}) ===")
            initrd = self.make_initrd(
                "bzip2.elf", app_args=[level, "-k", "-f", f"/tmp/{name}{ext}"]
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}{ext}")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    self.run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        docker=False,
                    )

                    self.run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        docker=False,
                        timeout=120,
                    )
                print(f"  PASS: {name} compress")
            except SystemExit:
                print(f"  FAIL: {name} compress")
                failed.append(f"{name} compress")
            finally:
                if initrd.exists():
                    initrd.unlink()

        for name in _decompress_samples:
            test_file = self.repo_root / "tests" / f"{name}.bz2"
            # Use -ds for sample3 (small-memory decompression mode).
            decompress_flag = "-ds" if name == "sample3" else "-d"
            print(f"=== bzip2 {machine} standalone decompress test ({name}) ===")
            initrd = self.make_initrd(
                "bzip2.elf",
                app_args=[decompress_flag, "-k", "-f", f"/tmp/{name}.bz2"],
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}.bz2")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    self.run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                        docker=False,
                    )

                    self.run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        docker=False,
                        timeout=120,
                    )
                print(f"  PASS: {name} decompress")
            except SystemExit:
                print(f"  FAIL: {name} decompress")
                failed.append(f"{name} decompress")
            finally:
                if initrd.exists():
                    initrd.unlink()

        if failed:
            msg = ", ".join(failed)
            raise RuntimeError(
                f"bzip2 Windows {machine} standalone tests FAILED: {msg}"
            )
        print(f"=== All bzip2 Windows {machine} standalone tests PASSED ===")

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
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    Bzip2Build.main()
