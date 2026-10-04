import sys

if sys.platform != "darwin":
    raise SystemExit("codex-usage-bar currently supports macOS only. Windows support is planned.")

from .manager import main
raise SystemExit(main())
