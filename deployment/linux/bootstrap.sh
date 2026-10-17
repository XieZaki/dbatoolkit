#!/usr/bin/env sh
set -eu

database="all-db"
python_cmd="python3"
venv_path=".venv"
offline=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --offline) offline=1 ;;
    --database) shift; database="${1:-}" ;;
    --python) shift; python_cmd="${1:-}" ;;
    --venv) shift; venv_path="${1:-}" ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$database" in all-db|sqlserver|postgresql|mysql|oracle|mongodb|redis) ;; *) echo "Invalid database extra" >&2; exit 2 ;; esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
wheelhouse="$project_root/deployment/wheelhouse"
venv="$project_root/$venv_path"
venv_python="$venv/bin/python"

host_runtime=$("$python_cmd" -c "import sys,struct; assert (3,11) <= sys.version_info[:2] < (3,14), 'Python >=3.11,<3.14 required'; print('{}.{}:{}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))")
echo "Host Python runtime: $host_runtime"
if [ -d "$venv" ]; then
  [ -x "$venv_python" ] || { echo "Existing $venv is not a compatible Linux venv. Move it aside and rebuild explicitly." >&2; exit 1; }
  venv_runtime=$("$venv_python" -c "import sys,struct; print('{}.{}:{}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))")
  [ "$venv_runtime" = "$host_runtime" ] || { echo "Existing venv runtime $venv_runtime does not match host $host_runtime. Move it aside and rebuild explicitly." >&2; exit 1; }
  echo "Reusing existing venv: $venv"
else
  "$python_cmd" -m venv "$venv"
fi

[ -d "$wheelhouse" ] || { echo "Wheelhouse missing: $wheelhouse" >&2; exit 1; }
set -- "$wheelhouse"/*.whl
[ -e "$1" ] || { echo "Wheelhouse contains no wheels for this OS/Python/architecture." >&2; exit 1; }

# Installation is local-only in both modes; there is no network or system-package fallback.
"$venv_python" -m pip install --no-index --find-links "$wheelhouse" -e "$project_root[$database]"
"$venv_python" -m dba doctor
echo "Next: $venv_python -m dba validate"
echo "Toolkit does not install ODBC system packages. Use your approved OS administration process."
find "$project_root/deployment/packages" -maxdepth 1 -type f ! -name README.md -print 2>/dev/null || true
[ "$offline" -eq 0 ] || echo "Offline mode completed without package-index access."
