# -*- coding: utf-8 -*-
"""main.pyw — 더블클릭 실행 진입점(콘솔 없음).

.pyw 연결 인터프리터(시스템 pythonw)에는 PySide6 가 없으므로, venv 밖에서
실행되면 .venv 의 pythonw.exe 로 자신을 재실행하고 즉시 종료한다.
GUI 본체는 rename_gui.main() 단일출처를 그대로 사용한다.
"""
import subprocess
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_VENV_PYW = _DIR / ".venv" / "Scripts" / "pythonw.exe"


def _alert(msg: str) -> None:
    """콘솔 없는 실행에서 유일한 오류 표시 수단(Win32 메시지박스)."""
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, msg, "mkvtool", 0x10)


def main() -> int:
    if Path(sys.prefix).resolve() != _VENV_PYW.parents[1].resolve():
        if not _VENV_PYW.exists():
            _alert(f"venv 인터프리터가 없다:\n{_VENV_PYW}")
            return 1
        subprocess.Popen([str(_VENV_PYW), str(_DIR / "main.pyw")], cwd=str(_DIR))
        return 0
    from rename_gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
