"""The tool registry: every MCP tool is a function registered with @tool.

A tool function takes the arguments dict and returns a result (results.text_result and friends). It may raise ToolError (a
failure with a message for the caller) or ValueError / KeyError / OSError (reported the same way).
"""
import json
import sys
from dataclasses import dataclass

from . import qemu, screen
from .inspector import InspectorError
from .results import ToolError, text_result


@dataclass
class Tool:
    name: str
    fn: object
    description: str
    props: dict
    required: tuple
    acting: bool = False       # an input tool: takes settle=true and waits for the screen to settle afterwards


TOOLS = {}

SETTLE_PROPS = {'settle': {'type': 'boolean', 'description': 'wait for the screen to change and settle before returning'},
                'timeout': {'type': 'number'}, 'stable_for': {'type': 'number'}, 'wait_change': {'type': 'number'}}


def tool(name, description, props=None, required=(), acting=False):
    def register(fn):
        if name in TOOLS:
            raise ValueError(f'tool {name} is registered twice')
        TOOLS[name] = Tool(name, _acting(fn) if acting else fn, description, props or {}, tuple(required), acting)
        return fn
    return register


def _acting(fn):
    def wrapper(args):
        use = bool(args.get('settle')) and qemu.alive()
        baseline = screen.grab_frame() if use else None
        result = fn(args)
        if use and not result.get('isError'):
            result['content'].append({'type': 'text', 'text': 'settle: ' + json.dumps(screen.settle(args, baseline))})
        return result
    return wrapper


def call(name, args):
    if name not in TOOLS:
        raise KeyError(name)
    try:
        return TOOLS[name].fn(args or {})
    except (ToolError, InspectorError) as e:
        return text_result(str(e), error=True)
    except (KeyError, ValueError, TypeError, OSError) as e:
        return text_result(f'{name}: {type(e).__name__}: {e}', error=True)
    except Exception as e:  # noqa: BLE001 - a bug in one tool or a bad argument must not end the server (the VM and the connections live in it)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return text_result(f'{name}: internal error, {type(e).__name__}: {e}', error=True)


def listing():
    out = []
    for t in TOOLS.values():
        props = {**t.props, **(SETTLE_PROPS if t.acting else {})}
        out.append({'name': t.name, 'description': t.description,
                    'inputSchema': {'type': 'object', 'properties': props, 'required': list(t.required)}})
    return out


def fraction(desc):
    """Schema of an argument that is a fraction of the screen."""
    return {'type': 'number', 'minimum': 0, 'maximum': 1, 'description': desc}


def unit(v, name):
    v = float(v)
    if not 0 <= v <= 1:
        raise ValueError(f'{name} must be a fraction of the screen between 0 and 1')
    return v
