"""Tool results in the shape MCP wants: {'content': [...], 'isError': bool}."""
import json


class ToolError(Exception):
    """A failure to report to the caller as an error result (not a bug): the message says what to do."""


def text_result(text, error=False):
    r = {'content': [{'type': 'text', 'text': text}]}
    if error:
        r['isError'] = True
    return r


def json_result(value):
    return text_result(json.dumps(value, ensure_ascii=False, indent=1, default=str))


def image_result(png_base64, caption):
    return {'content': [{'type': 'image', 'data': png_base64, 'mimeType': 'image/png'},
                        {'type': 'text', 'text': caption}]}
