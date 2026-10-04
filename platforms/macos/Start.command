#!/bin/zsh
set -eu
umask 077
script_dir=${0:A:h}
resource_root=${script_dir:h:h}
cd "$resource_root"

print 'codex-usage-bar'
print 'Starts your regular Codex with its existing login, settings, and conversation history.'
print 'The usage bar stays above the native composer on home and conversation pages.'
print 'An instance already started by this launcher can reconnect without restarting.'
print 'If Codex was opened directly, save your work and quit it with Cmd-Q once; this launcher waits.'
print 'Press Ctrl-C here to remove the bar and leave Codex running.'
print 'The local debug port stays open until you quit Codex with Cmd-Q.'
print ''

python_path=''
for candidate in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3(N) \
                 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [[ -x "$candidate" ]] && "$candidate" -I -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
        python_path=$candidate
        break
    fi
done
if [[ -z "$python_path" ]]; then
    print -u2 'Python 3.12 or later is required. No interpreter has been installed or changed.'
    exit 1
fi

# Ignore Python environment overrides and user-site packages; leave no .pyc in the app.
exec "$python_path" -E -s -B -m codex_bar daily --acknowledge-runtime --wait-for-exit
