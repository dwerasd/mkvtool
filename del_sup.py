# -*- coding: utf-8 -*-
"""del_sup.py — 지정 폴더 내 모든 MKV 에서 자막 트랙을 제거하고 재합치기한다.

사용법:
    python del_sup.py "<폴더경로>" [<폴더경로2> ...]

동작:
    1. 폴더를 재귀 탐색해 *.mkv 파일을 수집한다.
    2. mkvmerge -J 로 트랙을 검사한다. 자막 트랙이 0개면 스킵한다.
    3. 자막이 1개 이상이면 --no-subtitles 로 같은 폴더에 임시 파일로 재합치기한다.
    4. 성공 시 임시 파일로 원본을 원자적으로 교체한다(원본 제거 + 원본명 유지).
       실패 시 원본은 보존하고 임시 파일만 삭제한다.
"""

import io
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 콘솔 인코딩(cp949 등)에 없는 문자(em-dash, 특수 파일명 등)로 print 가 죽지 않도록 대체 출력
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="replace")

TMP_SUFFIX = ".delsup.tmp.mkv"
ENV_FILE = Path(__file__).resolve().parent / ".env"
FAIL_LIST_MAX = 20  # 텔레그램 메시지에 나열할 실패 파일 상한


@dataclass
class Result:
    """처리 결과 집계. src/out_bytes 는 완료 건의 원본/출력 총 바이트."""
    done: int = 0
    skipped: int = 0
    failed: int = 0
    src_bytes: int = 0
    out_bytes: int = 0
    failed_files: list[str] = field(default_factory=list)

    def merge(self, other: "Result") -> None:
        self.done += other.done
        self.skipped += other.skipped
        self.failed += other.failed
        self.src_bytes += other.src_bytes
        self.out_bytes += other.out_bytes
        self.failed_files.extend(other.failed_files)


def load_env(path: Path) -> dict[str, str]:
    """KEY=VALUE 형식의 .env 를 파싱한다. 주석(#)·빈 줄 무시."""
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


# mkvmerge 위치는 .env 의 PATH_MKV_TOOLS 를 따른다(미설정 시 스크립트 폴더)
MKVMERGE = Path(
    load_env(ENV_FILE).get("PATH_MKV_TOOLS") or Path(__file__).resolve().parent
) / "mkvmerge.exe"


def format_size(n: float) -> str:
    """바이트를 사람이 읽기 쉬운 단위 문자열로 변환한다."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{int(n):,}B" if unit == "B" else f"{n:,.1f}{unit}"
        n /= 1024
    return f"{n:,.1f}TB"


def format_elapsed(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}시간 {m}분 {s}초"
    return f"{m}분 {s}초" if m else f"{s}초"


def build_summary(roots: list[str], total: Result, elapsed: float) -> str:
    """텔레그램 전송용 작업 요약을 만든다(4096자 제한 대응 절단 포함)."""
    lines = ["🎬 del_sup 자막 제거 완료"]
    lines += [f"대상: {r}" for r in roots]
    lines.append(
        f"완료 {total.done} / 스킵(자막없음) {total.skipped} / 실패 {total.failed}"
    )
    if total.done:
        saved = total.src_bytes - total.out_bytes
        lines.append(
            f"용량 {format_size(total.src_bytes)} → {format_size(total.out_bytes)}"
            f" (절감 {format_size(saved)})"
        )
    lines.append(f"소요 {format_elapsed(elapsed)}")
    if total.failed_files:
        lines.append("실패 목록:")
        lines += [f"- {name}" for name in total.failed_files[:FAIL_LIST_MAX]]
        if len(total.failed_files) > FAIL_LIST_MAX:
            lines.append(f"...외 {len(total.failed_files) - FAIL_LIST_MAX}건")
    text = "\n".join(lines)
    return text[:4000] + "\n…(생략)" if len(text) > 4000 else text


def send_telegram(text: str) -> bool:
    """텔레그램 봇 API 로 메시지를 보낸다. 미설정/전송 실패 시 False(작업엔 영향 없음)."""
    env = load_env(ENV_FILE)
    serv = (env.get("TELE_SERV") or "api.telegram.org")
    serv = serv.removeprefix("https://").removeprefix("http://").rstrip("/")
    token = env.get("TELE_TOKEN", "")
    chat_id = env.get("TELE_CHAT_ID", "")
    if not token or not chat_id:
        print("[텔레그램] .env 의 TELE_TOKEN/TELE_CHAT_ID 미설정 — 알림 생략")
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(f"https://{serv}/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
    except (OSError, ValueError) as e:
        print(f"[텔레그램] 전송 실패: {e}")
        return False
    if not ok:
        print("[텔레그램] 전송 실패: API 응답 ok=false")
        return False
    print("[텔레그램] 완료 메시지 전송됨")
    return True


def probe_tracks(mkv: Path) -> dict | None:
    """mkvmerge -J 로 트랙 유형별 개수를 반환한다. 검사 실패 시 None."""
    proc = subprocess.run(
        [str(MKVMERGE), "-J", str(mkv)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode == 2:
        print(f"  [검사실패] {mkv.name}: {proc.stderr.strip() or proc.stdout.strip()}")
        return None
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"  [검사실패] {mkv.name}: JSON 파싱 불가")
        return None
    counts = {"video": 0, "audio": 0, "subtitles": 0}
    for t in info.get("tracks", []):
        kind = t.get("type")
        if kind in counts:
            counts[kind] += 1
    return counts


def strip_subtitles(mkv: Path, src: dict) -> tuple[int, int] | None:
    """자막 트랙을 제거한 임시 파일을 만들고 원본을 교체한다.

    성공 시 (원본 바이트, 출력 바이트), 실패 시 None 반환.
    """
    tmp = mkv.with_name(mkv.stem + TMP_SUFFIX)
    if tmp.exists():
        tmp.unlink()  # 이전 실행 잔재 제거

    # 진행률을 사용자에게 보여주기 위해 출력을 캡처하지 않는다
    proc = subprocess.run(
        [str(MKVMERGE), "-o", str(tmp), "--no-subtitles", str(mkv)],
    )
    if proc.returncode == 2 or not tmp.exists():
        print(f"  [합치기실패] {mkv.name} (종료코드 {proc.returncode}) — 원본 보존")
        if tmp.exists():
            tmp.unlink()
        return None
    if proc.returncode == 1:
        print(f"  [경고] {mkv.name}: mkvmerge 경고 발생(출력물은 유효)")

    # 출력물 검증: 비디오/오디오 수는 원본과 동일, 자막은 0 이어야 한다
    out = probe_tracks(tmp)
    if (
        out is None
        or out["subtitles"] != 0
        or out["video"] != src["video"]
        or out["audio"] != src["audio"]
    ):
        print(
            f"  [검증실패] {mkv.name}: 원본 V{src['video']}/A{src['audio']} vs "
            f"출력 {out and f'V{out['video']}/A{out['audio']}/S{out['subtitles']}'}"
            f" — 원본 보존, 임시 파일 삭제"
        )
        tmp.unlink()
        return None

    src_size = mkv.stat().st_size
    out_size = tmp.stat().st_size
    tmp.replace(mkv)  # 원자적 교체 = 원본 제거 + 원본 파일명 유지
    print(f"  [완료] {mkv.name} ({src_size:,}B → {out_size:,}B)")
    return src_size, out_size


def process_dir(root: Path) -> Result:
    """폴더를 재귀 처리한 결과 집계를 반환한다."""
    res = Result()
    files = sorted(p for p in root.rglob("*.mkv") if not p.name.endswith(TMP_SUFFIX))
    print(f"[대상] {root} — MKV {len(files)}개")
    for mkv in files:
        src = probe_tracks(mkv)
        if src is None:
            res.failed += 1
            res.failed_files.append(mkv.name)
            continue
        if src["subtitles"] == 0:
            print(f"  [스킵] {mkv.name}: 자막 없음")
            res.skipped += 1
            continue
        print(f"  [처리] {mkv.name}: 자막 {src['subtitles']}개 제거 시작")
        sizes = strip_subtitles(mkv, src)
        if sizes:
            res.done += 1
            res.src_bytes += sizes[0]
            res.out_bytes += sizes[1]
        else:
            res.failed += 1
            res.failed_files.append(mkv.name)
    return res


def main() -> int:
    if len(sys.argv) < 2:
        print(f"사용법: python {Path(sys.argv[0]).name} \"<폴더경로>\" [<폴더경로2> ...]")
        return 2
    if not MKVMERGE.exists():
        print(f"[오류] mkvmerge.exe 를 찾을 수 없음: {MKVMERGE}")
        return 2

    started = time.monotonic()
    total = Result()
    roots: list[str] = []
    for arg in sys.argv[1:]:
        root = Path(arg)
        roots.append(str(root))
        if not root.is_dir():
            print(f"[오류] 폴더가 아니거나 접근 불가: {arg}")
            total.failed += 1
            total.failed_files.append(arg)
            continue
        total.merge(process_dir(root))

    elapsed = time.monotonic() - started
    print(f"\n[요약] 완료 {total.done} / 스킵(자막없음) {total.skipped} / 실패 {total.failed}")
    send_telegram(build_summary(roots, total, elapsed))
    return 0 if total.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
