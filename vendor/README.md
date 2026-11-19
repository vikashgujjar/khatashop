# Bundled libraries

The server cannot install packages with pip, so the one library the app needs that is not part
of Python itself is shipped here and loaded from this folder (see `app/__init__.py`).

| Folder | Library | Version | Licence |
|---|---|---|---|
| `mysql/` | MySQL Connector/Python (pure-Python part only) | 9.1.0 | GPLv2 with the Universal FOSS Exception (`mysql/LICENSE.txt`) |

Only the Python source is included (no C extension, no Windows DLLs), so it works on any
operating system with Python 3.9+. To update: `pip download mysql-connector-python==<version>`,
then copy `mysql/__init__.py` and `mysql/connector/` (without `aio/` and `django/`) here.
