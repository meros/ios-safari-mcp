"""The MCP protocol: JSON-RPC 2.0 over stdio, one message per line."""
import json
import sys

from . import __version__, registry, tools  # noqa: F401  (tools registers the tools)

PROTOCOL = '2024-11-05'


def handle(msg):
    """The result for one request (None for a notification); raises LookupError for an unknown method."""
    method = msg.get('method')
    mid = msg.get('id')
    if method == 'initialize':
        return {'protocolVersion': (msg.get('params') or {}).get('protocolVersion', PROTOCOL),
                'capabilities': {'tools': {}}, 'serverInfo': {'name': 'ios-safari-mcp', 'version': __version__}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': registry.listing()}
    if method == 'tools/call':
        params = msg.get('params') or {}
        return registry.call(params['name'], params.get('arguments'))
    if mid is None:
        return None            # a notification
    raise LookupError(method)


def serve(stdin=None, stdout=None):
    from . import config
    config.ensure_dirs()
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        try:
            result = handle(msg)
        except (LookupError, KeyError) as e:
            stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg.get('id'),
                                     'error': {'code': -32601, 'message': f'unknown method or tool {e}'}}) + '\n')
            stdout.flush()
            continue
        if msg.get('id') is not None and result is not None:
            stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}) + '\n')
            stdout.flush()
