#!/usr/bin/env sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
venv_path="${1:-.venv}"
python="$project_root/$venv_path/bin/python"
[ -x "$python" ] || { echo "Runtime missing: $python. Run bootstrap.sh first." >&2; exit 1; }
exec "$python" -m dba doctor
