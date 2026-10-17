#!/usr/bin/env sh
set -eu

database="all-db"
python_cmd="python3"
venv_path=".venv"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --database) shift; database="${1:-}" ;;
    --python) shift; python_cmd="${1:-}" ;;
    --venv) shift; venv_path="${1:-}" ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$database" in all-db|sqlserver|postgresql|mysql|oracle|mongodb|redis) ;; *)
  echo "Invalid database extra: $database" >&2; exit 2 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
bootstrap="$project_root/deployment/linux/bootstrap.sh"

echo "Upgrade uses the current local checkout at $project_root and its local wheelhouse."
echo "Review release notes and preserve local Inventory and OS Credential Store entries."
sh "$bootstrap" --offline --database "$database" --python "$python_cmd" --venv "$venv_path"
venv_python="$project_root/$venv_path/bin/python"
"$venv_python" -m dba doctor
"$venv_python" -m dba validate
echo "Upgrade checks passed. Local Inventory and credential storage were not modified."
