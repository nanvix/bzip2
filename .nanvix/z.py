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

from nanvix_zutil import CFG_GH_TOKEN, CFG_SYSROOT, EXIT_MISSING_DEP, Sysroot, ZScript, log

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"


class Bzip2Build(ZScript):
    """Build script for nanvix/bzip2."""

    def _make_args(self, *targets: str) -> list[str]:
        """Build the common make argument list."""
        nanvix_sysroot = self.config.get(CFG_SYSROOT, "")
        if not nanvix_sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )

        args = [
            "make", "-f", "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={nanvix_sysroot}",
        ]

        args.extend([
            f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
            f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
            f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
        ])

        args.extend(targets)
        return args

    def setup(self) -> None:
        """Download the Nanvix sysroot and persist its path."""
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag="latest",
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Build libbz2.a static library."""
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the bzip2 test suite.

        Without targets, runs the full suite (smoke + integration + functional).
        With targets (e.g. ``./z test -- test-smoke test-integration``), passes
        them directly to the Makefile.
        """
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def release(self) -> None:
        """Package the bzip2 release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)


if __name__ == "__main__":
    Bzip2Build.main()
