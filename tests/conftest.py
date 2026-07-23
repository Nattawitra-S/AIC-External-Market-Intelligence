"""
tests/conftest.py
==================
Installs a shared mysql.connector stub ONCE, before any test module in this
directory is collected/imported.

Why this exists: ETL.lib_etl_mysql does `import mysql.connector` at module
level, and Python caches that binding the first time the module is
imported anywhere in the process. If two test files each try to stub
mysql.connector independently (each creating its own local module object
and using sys.modules.setdefault), whichever file's import runs first
"wins" the sys.modules slot -- the other file's local stub variable becomes
orphaned, and patches applied to it silently have no effect on the real
mysql.connector reference ETL.lib_etl_mysql actually holds. pytest always
imports conftest.py before collecting test modules in its directory, so
doing the stubbing here (once) removes the file-collection-order race
entirely. Test modules that need to patch connector behaviour should patch
`sys.modules["mysql.connector"]`'s attributes directly (or import it) rather
than creating their own separate stub module object.
"""
import sys
import types
from unittest.mock import MagicMock


class _MySQLError(Exception):
    def __init__(self, msg="", errno=0):
        super().__init__(msg)
        self.errno = errno


if "mysql.connector" not in sys.modules:
    mysql_stub = types.ModuleType("mysql")
    connector_stub = types.ModuleType("mysql.connector")
    connector_stub.Error = _MySQLError
    connector_stub.connect = MagicMock()
    mysql_stub.connector = connector_stub
    sys.modules["mysql"] = mysql_stub
    sys.modules["mysql.connector"] = connector_stub
    sys.modules["mysql.connector.pooling"] = types.ModuleType("mysql.connector.pooling")
