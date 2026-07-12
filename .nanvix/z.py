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
    EXIT_INVALID_ARGS,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    DockerConfig,
    ZScript,
    is_windows,
    log,
    make_initrd,
    run,
)
from nanvix_zutil.helpers import InitRdArgs
from nanvix_zutil.paths import (
    bin_out,
    dist_dir,
    include_out,
    lib_out,
    nanvix_root,
    out_dir,
    repo_root,
    test_out,
)

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class Bzip2Build(ZScript):
    """Build script for nanvix/bzip2."""

    # Build-time headers, libraries, startup objects, and linker scripts come
    # from the SDK. The downloaded sysroot is used only to run tests.
    SYSROOT_REQUIRED_FILES = (
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )
    SYSROOT_REQUIRED_FILES_WINDOWS = (
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

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
        toolchain_p = TOOLCHAIN_CONTAINER_PATH
        sysroot_p = (
            self.docker.translate_path(Path(sysroot)) if self.docker else Path(sysroot)
        )

        def translate(p: Path):
            return self.docker.translate_path(p) if self.docker else p

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
            f"NANVIX_ROOT={translate(nanvix_root())}",
            f"OUT_DIR={translate(out_dir())}",
            f"DIST_DIR={translate(dist_dir())}",
            f"LIB_OUT={translate(lib_out())}",
            f"INCLUDE_OUT={translate(include_out())}",
            f"BIN_OUT={translate(bin_out())}",
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
        # Build artifacts produced inside the container that must be copied
        # back to the host workspace so that `./z test` and `./z release`
        # can find them. Paths are relative to the workspace mount root
        # (i.e. repo_root()).
        root = repo_root()
        output_files = [
            # In-tree build artifacts (legacy locations used by tests).
            "libbz2.a",
            "bzip2.elf",
            # Installed artifacts staged for `./z release` / packaging.
            str((lib_out() / "libbz2.a").relative_to(root)),
            str((include_out() / "bzlib.h").relative_to(root)),
            str((bin_out() / "bzip2.elf").relative_to(root)),
        ]
        return dataclasses.replace(cfg, output_files=output_files)

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
        """Cross-compile libbz2.a and bzip2.elf for Nanvix."""
        run(*self._make_args("all", "bzip2.elf"), cwd=repo_root(), docker=self.docker)
        # Stage bzip2.elf into test_out() so the windows-test artifact
        # upload in nanvix/workflows@v2.3.0 picks it up (upload glob is
        # `.nanvix/out/test/**/*.{elf,so}`). The Makefile installs to
        # BIN_OUT for the release tree; we keep that path intact and add
        # the test_out copy here on the host. Works for both native and
        # docker builds because `output_files` already copies bzip2.elf
        # back to repo_root() on Windows.
        test_out().mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root() / "bzip2.elf", test_out() / "bzip2.elf")

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    # Test targets accepted on the CLI.  Only functional tests remain
    # after the smoke/integration tiers were removed from Makefile.nanvix.
    _SUPPORTED_TEST_TARGETS = frozenset({"test", "test-functional"})

    def test(self) -> None:
        """Run the functional test suite.

        Compresses and decompresses sample data through the bzip2 binary
        running under nanvixd.  Only the standalone deployment mode is
        supported, so the run is always driven from Python (via
        :func:`make_initrd`).

        Any CLI-supplied targets (``./z test <target>...``) must be a
        subset of :attr:`_SUPPORTED_TEST_TARGETS`; unknown targets are
        rejected so they are not silently dropped.
        """
        targets = self.targets or []
        unknown = [t for t in targets if t not in self._SUPPORTED_TEST_TARGETS]
        if unknown:
            log.fatal(
                f"Unsupported test target(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(self._SUPPORTED_TEST_TARGETS))}.",
                code=EXIT_INVALID_ARGS,
            )

        if is_windows():
            self._run_tests_windows()
            return

        self._run_functional_standalone()

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling bzip2.elf with system daemons via
        make_initrd, and a ramfs providing /tmp with test data files.
        """
        bzip2_elf = repo_root() / "bzip2.elf"
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
            test_file = repo_root() / "tests" / f"{name}{ext}"
            print(f"  Running bzip2 compress test ({name}, {level})...")
            initrd = make_initrd(
                self,
                repo_root() / "bzip2.elf",
                test_out(),
                args=InitRdArgs(app_args=[level, "-k", "-f", f"/tmp/{name}{ext}"]),
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}{ext}")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    run(
                        str(sysroot_path / "bin" / "nanvixd.elf"),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
                        timeout=120,
                    )
            finally:
                if initrd.exists():
                    initrd.unlink()
            print(f"  PASS: {name} compress")

        for name in _decompress_samples:
            test_file = repo_root() / "tests" / f"{name}.bz2"
            # Use -ds for sample3 (small-memory decompression mode).
            decompress_flag = "-ds" if name == "sample3" else "-d"
            print(f"  Running bzip2 decompress test ({name})...")
            initrd = make_initrd(
                self,
                repo_root() / "bzip2.elf",
                test_out(),
                args=InitRdArgs(
                    app_args=[decompress_flag, "-k", "-f", f"/tmp/{name}.bz2"],
                ),
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}.bz2")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    run(
                        str(sysroot_path / "bin" / "nanvixd.elf"),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
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

        Only the standalone deployment mode is supported.
        """
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

        # Locate bzip2.elf: prefer the windows-ci artifact overlay at
        # `test_out()`, fall back to a repo-root build.
        bzip2_elf_src: Path | None = None
        for candidate in [test_out() / "bzip2.elf", repo_root() / "bzip2.elf"]:
            if candidate.is_file():
                bzip2_elf_src = candidate
                break
        if bzip2_elf_src is None:
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
            p = repo_root() / "tests" / f"{name}{ext}"
            if not p.is_file():
                log.fatal(
                    f"Test data not found (tests/{name}{ext}).",
                    code=EXIT_MISSING_DEP,
                )
        for name in _decompress_samples:
            p = repo_root() / "tests" / f"{name}.bz2"
            if not p.is_file():
                log.fatal(
                    f"Test data not found (tests/{name}.bz2).",
                    code=EXIT_MISSING_DEP,
                )

        failed: list[str] = []

        for name, ext, level in _compress_samples:
            test_file = repo_root() / "tests" / f"{name}{ext}"
            print(f"=== bzip2 {machine} standalone compress test ({name}) ===")
            initrd = make_initrd(
                self,
                bzip2_elf_src,
                test_out(),
                args=InitRdArgs(app_args=[level, "-k", "-f", f"/tmp/{name}{ext}"]),
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}{ext}")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
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
            test_file = repo_root() / "tests" / f"{name}.bz2"
            # Use -ds for sample3 (small-memory decompression mode).
            decompress_flag = "-ds" if name == "sample3" else "-d"
            print(f"=== bzip2 {machine} standalone decompress test ({name}) ===")
            initrd = make_initrd(
                self,
                bzip2_elf_src,
                test_out(),
                args=InitRdArgs(
                    app_args=[decompress_flag, "-k", "-f", f"/tmp/{name}.bz2"],
                ),
            )
            try:
                with tempfile.TemporaryDirectory(prefix="nanvix_bzip2_") as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    shutil.copy2(test_file, ramfs_dir / "tmp" / f"{name}.bz2")
                    ramfs_img = tmpdir_path / "rootfs.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    run(
                        str(nanvixd),
                        "-bin-dir",
                        str(sysroot_path / "bin"),
                        "-ramfs",
                        str(ramfs_img),
                        "--",
                        str(initrd),
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
    # Clean
    # ------------------------------------------------------------------

    def clean(self) -> None:
        """Remove build artifacts."""
        if is_windows():
            for pattern in ["*.o", "*.a", "*.elf"]:
                for f in repo_root().glob(pattern):
                    f.unlink()
            dist_dir = repo_root() / "dist"
            if dist_dir.exists():
                shutil.rmtree(dist_dir)
            print("Cleaned build artifacts")
            return
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=repo_root(),
        )


if __name__ == "__main__":
    Bzip2Build.main()
