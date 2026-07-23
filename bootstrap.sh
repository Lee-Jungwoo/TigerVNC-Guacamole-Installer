#!/bin/sh
# Minimal, distribution-neutral launcher for the installer.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PYTHON=${URD_PYTHON:-}
INSTALL_PYTHON=0

for argument in "$@"; do
    if [ "$argument" = "--install-python" ]; then
        INSTALL_PYTHON=1
    fi
done

python_is_usable() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    if [ -n "$PYTHON" ] && command -v "$PYTHON" >/dev/null 2>&1 && python_is_usable "$PYTHON"; then
        return 0
    fi
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_usable "$candidate"; then
            PYTHON=$candidate
            return 0
        fi
    done
    return 1
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -- "$@"
    else
        printf '%s\n' 'error: root privileges are required to install Python 3.9+' >&2
        exit 77
    fi
}

bootstrap_python() {
    if [ -r /etc/os-release ]; then
        # This parser does not evaluate the file.  Only the simple ID field is used.
        os_id=$(sed -n 's/^ID=//p' /etc/os-release | head -n 1 | tr -d "\"'")
    else
        os_id=
    fi

    case "$os_id" in
        debian|ubuntu|linuxmint|pop|kali|raspbian)
            as_root apt-get update
            as_root apt-get install -y python3
            ;;
        fedora|rhel|centos|rocky|almalinux|ol|amzn)
            if command -v dnf >/dev/null 2>&1; then
                as_root dnf -y install python3
            else
                as_root yum -y install python3
            fi
            ;;
        arch|manjaro|endeavouros)
            as_root pacman -Syu --needed --noconfirm python
            ;;
        opensuse*|sles)
            as_root zypper --non-interactive install python3
            ;;
        alpine)
            as_root apk add python3
            ;;
        *)
            printf '%s\n' 'error: unsupported bootstrap platform; install Python 3.9+ and rerun' >&2
            exit 69
            ;;
    esac
}

if ! find_python; then
    if [ "$INSTALL_PYTHON" -eq 1 ] || [ "${URD_BOOTSTRAP_PYTHON:-0}" = 1 ]; then
        bootstrap_python
        find_python || {
            printf '%s\n' 'error: Python was installed but no Python 3.9+ interpreter was found' >&2
            exit 69
        }
    else
        printf '%s\n' 'error: Python 3.9+ is required.' >&2
        printf '%s\n' 'Install it yourself, or rerun with --install-python.' >&2
        exit 69
    fi
fi

if [ ! -f "$SCRIPT_DIR/src/urd_installer/__main__.py" ]; then
    printf '%s\n' 'error: installer package is missing; clone or extract the complete repository' >&2
    printf '%s\n' 'Running init.sh by itself is no longer supported.' >&2
    exit 66
fi

# --install-python belongs to this bootstrapper and must not reach the CLI.  POSIX
# sh has no arrays, so rebuild positional parameters without eval.
set -- "$@"
umask 077
new_arg_file=$(mktemp "${TMPDIR:-/tmp}/urd-bootstrap.XXXXXX") || {
    printf '%s\n' 'error: could not create bootstrap argument file' >&2
    exit 73
}
trap 'rm -f "$new_arg_file"' EXIT HUP INT TERM
for argument in "$@"; do
    [ "$argument" = "--install-python" ] && continue
    printf '%s\n' "$argument" >> "$new_arg_file"
done
set --
while IFS= read -r argument; do
    set -- "$@" "$argument"
done < "$new_arg_file"
rm -f "$new_arg_file"
trap - 0 HUP INT TERM

PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
    exec "$PYTHON" -m urd_installer "$@"
