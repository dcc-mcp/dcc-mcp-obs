#!/usr/bin/env sh
set -eu

python_bin="${DCC_MCP_INSTALL_PYTHON:-python3}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
exec "$python_bin" "$script_dir/install.py" "$@"
