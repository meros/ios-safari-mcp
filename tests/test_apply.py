"""scripts/apply-to-tree.py against a small stand-in for the parent's tree: it must patch once, change nothing a second time, and
touch nothing when the target is wrong or the parent has changed."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'apply-to-tree.py'
GUEST = Path(__file__).resolve().parent.parent / 'guest'

BUILD_SH = ('\tcp com.redwood.demo.plist com.redwood.wifi.plist com.redwood.thermald.plist root/LaunchDaemons/\n'
            '\t[ -f obj/net_test ] && cp obj/net_test root/bin/\n')
MKJOBS = "DENY = ('watchdog', 'osanalytics', 'lockdown', 'audio', 'jetsamproperties',\n        'purplebuddy')\n"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / 'checkout'
    (root / 'guest').mkdir(parents=True)
    (root / 'guest' / 'wi_relay').write_bytes(b'\xcf\xfa\xed\xfe fake binary')
    shutil.copy(GUEST / 'com.redwood.wirelay.plist', root / 'guest')
    tree = tmp_path / 'hackios'
    (tree / 'userland').mkdir(parents=True)
    (tree / 'userland' / 'build.sh').write_text(BUILD_SH)
    (tree / 'userland' / 'mkjobs.py').write_text(MKJOBS)
    return root, tree


def run(root, *args, env=None):
    e = dict(os.environ, IOS_SAFARI_MCP_ROOT=str(root), **(env or {}))
    return subprocess.run([sys.executable, '-I', str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=e)


def snapshot(tree):
    return {str(p.relative_to(tree)): p.read_bytes() for p in sorted(tree.rglob('*')) if p.is_file()}


def test_patches_the_tree_and_a_second_run_changes_nothing(world):
    root, tree = world
    r = run(root, tree)
    assert r.returncode == 0, r.stderr
    ud = tree / 'userland'
    assert (ud / 'obj' / 'wi_relay').read_bytes().startswith(b'\xcf\xfa')
    assert (ud / 'com.redwood.wirelay.plist').exists()
    build = (ud / 'build.sh').read_text()
    assert 'obj/wi_relay' in build and 'com.redwood.wirelay.plist root/LaunchDaemons' in build
    assert "'jetsamproperties', 'webinspectord'," in (ud / 'mkjobs.py').read_text()
    after_first = snapshot(tree)
    r = run(root, tree)
    assert r.returncode == 0 and r.stdout.count('already patched') == 3, r.stdout
    assert snapshot(tree) == after_first


def test_a_directory_that_is_not_a_tree_is_left_alone(world, tmp_path):
    root, _ = world
    wrong = tmp_path / 'elsewhere'
    wrong.mkdir()
    r = run(root, wrong)
    assert r.returncode != 0 and 'not a hackios tree' in r.stderr
    assert list(wrong.iterdir()) == []


def test_an_empty_hackios_dir_is_not_the_current_directory(world, tmp_path):
    root, _ = world
    alone = tmp_path / 'alone' / 'checkout'          # no sibling tree to fall back to
    shutil.copytree(root, alone)
    here = tmp_path / 'cwd'
    here.mkdir()
    e = dict(os.environ, IOS_SAFARI_MCP_ROOT=str(alone), HACKIOS_DIR='')
    r = subprocess.run([sys.executable, '-I', str(SCRIPT)], capture_output=True, text=True, env=e, cwd=here)
    assert list(here.iterdir()) == []
    assert r.returncode != 0


def test_a_changed_parent_stops_before_anything_is_written(world):
    root, tree = world
    (tree / 'userland' / 'mkjobs.py').write_text("DENY = ('watchdog',)\n")      # the line to patch is gone
    before = snapshot(tree)
    r = run(root, tree)
    assert r.returncode != 0 and 'not there exactly once' in r.stderr
    assert snapshot(tree) == before          # not even the binary or the plist was copied


def test_the_relay_must_be_built(world):
    root, tree = world
    (root / 'guest' / 'wi_relay').unlink()
    before = snapshot(tree)
    r = run(root, tree)
    assert r.returncode != 0 and 'not built' in r.stderr and snapshot(tree) == before
