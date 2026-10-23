# Contributing

Small, focused changes are easiest to review. Before you open a pull request:

1. `nix flake check` passes (tests, lint, the guest relay builds).
2. New behaviour has a test against the fakes in `tests/fakes.py`. Say in the description what you ran against a real guest and what you did not.
3. Claims about iOS behaviour come from a run, with the numbers; mark the rest as unverified.

The guest runs unmodified iOS: do not add Apple binaries, snapshots or dyld cache extracts to the repository. See [docs/development.md](docs/development.md).
