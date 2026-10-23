# Development

```sh
nix develop                      # python, pytest, ruff, clang, lld, ldid
pytest tests                     # 38 tests, no VM, about 25 s
ruff check .
nix flake check                  # builds the package (and runs the tests in the sandbox), the relay, and lint
bin/ios-safari-mcp doctor        # the setup, with the fix for each problem
```

Without `nix develop`: `PYTHONPATH=src python3 -m pytest tests` needs only pytest.

## Layout

`src/ios_safari_mcp/` is the package (see [architecture.md](architecture.md) for the modules), `guest/` the program for the guest,
`scripts/` the build and launch scripts, `tests/` the tests (`tests/live/` needs a VM), `docs/` this.

## Adding a tool

A tool is a function registered in one of `src/ios_safari_mcp/tools/*.py`:

```python
@tool('name', 'One sentence for the agent: what it does and what it returns.',
      {'arg': {'type': 'string', 'description': '...'}}, required=['arg'])
def name(args):
    ...
    return json_result({...})        # or text_result(...), or raise ToolError('what to do')
```

`acting=True` marks an input tool: it gets `settle=true` and waits for the screen afterwards. Add a test against the fake relay
(`tests/fakes.py`) or the fake monitor; `tests/test_core.py` checks that every tool has a description and a schema.

## Tests

- **Offline** (`tests/test_*.py`): `fakes.FakeRelay` speaks the relay's frames and answers the inspector protocol for a few
  expressions, with a small model of the page (its address, a keyboard that a tap opens and `blur()` closes). `fakes.FakeMonitor`
  stands in for QEMU's monitor. `conftest.py` uses QEMU instance 9 (ports 4534, 4535, 27843) and a temporary state directory, so a
  test never touches a VM on the default ports.
- **Fixtures** (`tests/fixtures/`): `handshake.json` holds two messages exactly as Safari sent them. To capture more, run a client
  with `Inspector(port, record='frames.jsonl')` against a live VM and copy the file to `tests/fixtures/traffic.jsonl`; the decoder test
  picks it up. Do not commit page content you do not own.
- **Live** (`tests/live/`): `smoke.py [URL] [FIELD_SELECTOR]` runs the tools through one long-lived MCP session and prints a time per
  step; `call.py TOOL '{json}'` calls one tool.

## Debugging the inspector

`Inspector(port, debug=True)` prints every message in both directions (the first 400 characters). The relay logs to the guest console,
which `vm_start` writes to `~/.local/state/ios-safari-mcp/run.log` (`log_tail pattern=wi_relay`). `bplist15.py` decodes a frame from the
command line: `python3 -m ios_safari_mcp.inspector.bplist15 <hex>`.

To see what a Safari process sends that nobody handles yet, record frames as above and read them with the decoder.

## The guest relay

`guest/wi_relay.c` is C with its own prototypes; add a symbol you use to `guest/libSystem.tbd` (or `CoreFoundation.tbd`), else the link
fails with `undefined symbol`. Build with `scripts/build-guest.sh` or `nix build .#wi-relay`; both use `-Wall -Werror`. The image picks
up the binary with `scripts/build-image.sh` (a boot after that uses it).

## Releasing

Bump the version in `pyproject.toml`, `flake.nix` and `src/ios_safari_mcp/__init__.py`, add a `CHANGELOG.md` entry, run
`nix flake check`, and run `tests/live/smoke.py` against a fresh image. State in the README's Status table what the smoke run covered.
