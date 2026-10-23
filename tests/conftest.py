"""Test setup: the package from src/, a throw-away state directory, and QEMU instance 9 (ports 4534, 4535, 27843), so a test
never touches a VM that is running on the default ports."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)
os.environ['IOS_SAFARI_MCP_STATE'] = tempfile.mkdtemp(prefix='ios-safari-mcp-test-')
os.environ['QEMU_INSTANCE'] = '9'
os.environ.pop('WI_PORT', None)
