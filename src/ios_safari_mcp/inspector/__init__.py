"""Web Inspector access: the client (client.py), the property list format of the apps (bplist15.py), the JavaScript
that the tools run in the page (page.py) and the shared connection (manager.py)."""
from .client import CONTROL_PEER, Inspector, InspectorError, NavigationError, Session, decode, encode

__all__ = ['CONTROL_PEER', 'Inspector', 'InspectorError', 'NavigationError', 'Session', 'decode', 'encode']
