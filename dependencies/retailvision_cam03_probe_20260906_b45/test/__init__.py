import sys

from . import _test_bootstrap


sys.modules.setdefault("_test_bootstrap", _test_bootstrap)
