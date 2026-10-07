"""Keep the test suite away from the developer's real configuration.

zendesk_skill resolves its config/secrets/OAuth paths from Path.home() at import
time, and several tests save and delete credentials. Point HOME at a throwaway
directory before the package is imported so a test run can never read, modify or
delete real credentials in ~/.config/zd-cli.
"""

import atexit
import os
import shutil
import tempfile

_fake_home = tempfile.mkdtemp(prefix="zd-cli-test-home-")
atexit.register(shutil.rmtree, _fake_home, ignore_errors=True)
os.environ["HOME"] = _fake_home
for _var in ("ZENDESK_EMAIL", "ZENDESK_TOKEN", "ZENDESK_SUBDOMAIN"):
    os.environ.pop(_var, None)
