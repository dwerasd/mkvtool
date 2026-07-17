# -*- coding: utf-8 -*-
"""smi2srt.py — 폴더를 재귀 탐색해 .smi(SAMI) 자막을 .srt 로 일괄 변환한다.

동작:
    1. 폴더를 재귀 탐색해 *.smi 를 수집한다.
    2. 변환 엔진: .env 의 PATH_SUBTITLE 에 SubtitleEdit.exe 가 있으면 그것으로
       변환한다. 단 SE 는 인코딩 오감지 시 한글이 깨진 srt 를 무경고로 만들므로,
       원본에 한글이 있는데 출력에 없으면 산출물을 폐기하고 내부 파서로 폴백한다.
       SE 가 없으면 내부 파서를 쓴다(UTF-8 시도 → CP949 폴백, SYNC 파싱).
    3. 출력은 UTF-8 BOM .srt, 같은 폴더·같은 이름. 다국어 SAMI(내부 파서)는
       한국어 클래스(KR 포함) 우선, 없으면 큐가 가장 많은 클래스.
    4. 같은 이름의 .srt 가 이미 있으면 스킵한다(--overwrite 로 덮어쓰기).
       원본 .smi 는 삭제하지 않는다.

사용법:
    python smi2srt.py "<폴더경로>" [<폴더경로2> ...] [--overwrite]
"""

import html
import io
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# 콘솔 인코딩(cp949 등)에 없는 문자로 print 가 죽지 않도록 대체 출력
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="replace")


def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE 형식의 .env 를 파싱한다. 주석(#)·빈 줄 무시(del_sup.py 와 동일)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


_SCRIPT_DIR = Path(__file__).resolve().parent
_ENV = load_env(_SCRIPT_DIR / ".env")
SUBTITLE_EDIT = Path(_ENV.get("PATH_SUBTITLE") or _SCRIPT_DIR) / "SubtitleEdit.exe"

DEFAULT_LAST_MS = 4000  # 종료 마커 없는 마지막 큐의 표시 시간
HANGUL_RE = re.compile(r"[가-힣]")

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
SYNC_RE = re.compile(r"<sync[^>]*?start\s*=\s*[\"']?(\d+)", re.I)
P_RE = re.compile(r"<p(?:\s[^>]*?class\s*=\s*[\"']?([A-Za-z0-9_-]+)[^>]*)?>", re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]*>")


@dataclass
class Result:
    done: int = 0
    skipped: int = 0
    failed: int = 0

    def merge(self, other: "Result") -> None:
        self.done += other.done
        self.skipped += other.skipped
        self.failed += other.failed


def read_smi(path: Path) -> str:
    """UTF-8(BOM) 우선, 실패 시 CP949 로 디코딩한다."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def block_text(seg: str) -> str:
    """SYNC 블록 조각에서 표시 텍스트를 추출한다. 공백뿐이면 빈 문자열(종료 마커)."""
    seg = BR_RE.sub("\n", seg)
    seg = TAG_RE.sub("", seg)
    seg = html.unescape(seg)
    lines = [ln.strip() for ln in seg.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_smi(text: str) -> list[tuple[int, str]]:
    """SAMI 본문을 (시작ms, 텍스트) 큐 목록으로 파싱한다. 텍스트 "" = 종료 마커."""
    text = COMMENT_RE.sub("", text)
    syncs = list(SYNC_RE.finditer(text))
    cues: dict[str, list[tuple[int, str]]] = {}
    last_class = ""
    for i, m in enumerate(syncs):
        start = int(m.group(1))
        seg = text[m.end(): syncs[i + 1].start() if i + 1 < len(syncs) else len(text)]
        seg = seg[seg.find(">") + 1:]  # sync 태그 잔여부 제거
        ps = list(P_RE.finditer(seg))
        if not ps:  # P 태그 없는 변형 — 직전 클래스로 귀속
            cues.setdefault(last_class, []).append((start, block_text(seg)))
            continue
        for j, p in enumerate(ps):
            cls = (p.group(1) or last_class or "").upper()
            last_class = cls
            chunk = seg[p.end(): ps[j + 1].start() if j + 1 < len(ps) else len(seg)]
            cues.setdefault(cls, []).append((start, block_text(chunk)))

    if not cues:
        return []
    # 한국어 클래스 우선, 없으면 실큐(비어있지 않은 큐) 최다 클래스
    def score(cls: str) -> tuple[int, int]:
        real = sum(1 for _, t in cues[cls] if t)
        return ("KR" in cls, real)
    best = max(cues, key=score)
    return sorted(cues[best], key=lambda c: c[0])


def to_srt(cues: list[tuple[int, str]]) -> str:
    """큐 목록을 SRT 텍스트로 만든다. 다음 SYNC 시작이 곧 종료 시각."""
    def ts(ms: int) -> str:
        s, ms = divmod(ms, 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    out: list[str] = []
    n = 0
    for i, (start, txt) in enumerate(cues):
        if not txt:
            continue
        end = cues[i + 1][0] if i + 1 < len(cues) else start + DEFAULT_LAST_MS
        end = max(end, start + 1)
        n += 1
        out.append(f"{n}\n{ts(start)} --> {ts(end)}\n{txt}\n")
    return "\n".join(out)


def convert_with_se(smi: Path, srt: Path, src_text: str) -> int | None:
    """SubtitleEdit CLI 변환. 성공 시 큐 수, 실패(오감지 포함) 시 None.

    SE 는 인코딩 오감지 시 한글이 깨진 srt 를 무경고로 만들므로,
    원본에 한글이 있는데 출력에 없으면 산출물을 폐기한다(내부 파서 폴백용).
    """
    if srt.exists():
        srt.unlink()  # SE 는 대상 존재 시 _2.srt 를 만들므로 선삭제(overwrite 경로)
    proc = subprocess.run(
        [str(SUBTITLE_EDIT), "/convert", str(smi), "SubRip"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0 or not srt.exists():
        return None
    out_text = srt.read_text(encoding="utf-8-sig", errors="replace")
    if HANGUL_RE.search(src_text) and not HANGUL_RE.search(out_text):
        srt.unlink()  # 인코딩 오감지 산출물 폐기
        return None
    return out_text.count("-->")


def convert(smi: Path, overwrite: bool) -> tuple[str, int]:
    """단일 파일 변환. ('done'/'skip'/'fail', 큐 개수)를 반환한다(출력은 호출부 책임)."""
    srt = smi.with_suffix(".srt")
    if srt.exists() and not overwrite:
        return "skip", 0
    text = read_smi(smi)
    if SUBTITLE_EDIT.exists():
        cues_se = convert_with_se(smi, srt, text)
        if cues_se is not None:
            return "done", cues_se
    cues = parse_smi(text)
    body = to_srt(cues)
    if not body:
        return "fail", 0
    srt.write_text(body, encoding="utf-8-sig")
    return "done", body.count("-->")


def process_dir(root: Path, overwrite: bool) -> Result:
    res = Result()
    files = sorted(root.rglob("*.smi"))
    print(f"[대상] {root} — SMI {len(files)}개")
    for smi in files:
        try:
            state, cues = convert(smi, overwrite)
        except OSError as e:
            print(f"  [실패] {smi.name}: {e}")
            res.failed += 1
            continue
        if state == "done":
            print(f"  [완료] {smi.name} → {smi.with_suffix('.srt').name} (큐 {cues}개)")
            res.done += 1
        elif state == "skip":
            res.skipped += 1
        else:
            print(f"  [파싱실패] {smi.name}: 유효한 자막 큐 없음")
            res.failed += 1
    return res


def main() -> int:
    args = sys.argv[1:]
    overwrite = "--overwrite" in args
    roots = [a for a in args if a != "--overwrite"]
    if not roots:
        print(f'사용법: python {Path(sys.argv[0]).name} "<폴더경로>" [<폴더경로2> ...] [--overwrite]')
        return 2

    total = Result()
    for arg in roots:
        root = Path(arg)
        if not root.is_dir():
            print(f"[오류] 폴더가 아니거나 접근 불가: {arg}")
            total.failed += 1
            continue
        total.merge(process_dir(root, overwrite))

    print(f"\n[요약] 완료 {total.done} / 스킵(srt존재) {total.skipped} / 실패 {total.failed}")
    return 0 if total.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
