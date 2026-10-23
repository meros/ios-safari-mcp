"""Command line: `ios-safari-mcp [serve|doctor|build-image|version]` (serve is the default: the MCP server on stdio)."""
import argparse
import os
import subprocess
import sys

from . import __version__, config


def build_image():
    script = os.path.join(config.ROOT, 'scripts', 'build-image.sh')
    env = dict(os.environ, HACKIOS_DIR=config.HACKIOS, IOS_SAFARI_MCP_STATE=config.STATE, IOS_SAFARI_MCP_ROOT=config.ROOT)
    return subprocess.call([script], env=env)


def main(argv=None):
    p = argparse.ArgumentParser(prog='ios-safari-mcp', description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('serve', help='run the MCP server on stdin and stdout (the default)')
    sub.add_parser('doctor', help='check the setup and print the fix for each problem')
    sub.add_parser('build-image', help='build the relay, patch the hackios build and build the image the VM boots')
    sub.add_parser('version')
    args = p.parse_args(argv)
    cmd = args.cmd or 'serve'
    if cmd == 'version':
        print(__version__)
        return 0
    if cmd == 'doctor':
        from . import doctor
        return doctor.main()
    if cmd == 'build-image':
        return build_image()
    from . import mcp
    mcp.serve()
    return 0


if __name__ == '__main__':
    sys.exit(main())
