# -*- coding: utf-8 -*-
"""rename.py — 하위 폴더를 재귀 탐색해 방송 릴 파일명을 선호 형식으로 정리한다.

선호 형식:
    <제목>.[S??]E???[.날짜6자리][.한글부제].<해상도>[.x264|.x265]-<그룹>
    예) The.Return.of.Superman.E163.170101.720p-NEXT

규칙:
    1. 에피소드 마커(E163/S01E163)와 해상도 사이의 부제 처리(토큰 단위):
       한글 포함 토큰만 보존(직접 수정한 게스트명 — 엔시티127 등),
       영문 토큰은 제거한다(에피소드 부제, 로마자 병기 게스트명 등).
    2. HDTV/WEB-DL/AAC/REPACK/채널·플랫폼명(SBS, CP 등) 잡태그는 제거한다.
       기준점 뒤 기술영역은 날짜/해상도/코덱/한글 화이트리스트로 강제하므로
       미등록 태그(VIU 등)도 형식에 어긋나면 제거된다.
    3. 코덱은 x264/x265 로 통일해 해상도 바로 뒤에 붙인다
       (H.264/H264/AVC → x264, H.265/H265/HEVC → x265).
       MPEG/MPEG2/XviD/DivX(구형 릴)는 그대로 보존.
       코덱 표기가 없으면 x264 를 부여한다(x265 릴은 반드시 표기되므로).
    4. 에피소드 마커가 없으면 날짜(YYMMDD)를 기준점으로 삼는다(추석특집 등).
       둘 다 없으면 패턴불일치로 건너뛴다.
    5. 8자리 날짜(YYYYMMDD, 20100711)는 세기 접두(19/20)를 지워 6자리로 통일한다.

사용법:
    python rename.py "<폴더경로>" [<폴더경로2> ...] [--apply]
    --apply 없이 실행하면 미리보기(드라이런)만 한다.
"""

import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# 콘솔 인코딩(cp949 등)에 없는 문자로 print 가 죽지 않도록 대체 출력
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="replace")

EXTS = {".mkv", ".mp4", ".avi", ".ts", ".wmv", ".mov",
        ".srt", ".smi", ".ass", ".sub", ".idx"}

HANGUL_RE = re.compile(r"[가-힣]")
# 그룹명: 마지막 '-' 뒤 전체. 영숫자 시작, dot 포함 허용(SBS.VOD 표기),
# 특수문자 허용(HAN™). 기술 토큰이 섞이면 그룹이 아니다(transform 의 가드).
GROUP_RE = re.compile(r"-([A-Za-z0-9][^\-]*)$")
# 기준점 경계는 dot 외에 공백도 허용한다("결혼과 이혼 사이 E01.720p" 형태)
EP_RE = re.compile(r"(?<=[.\s])((?:[Ss]\d{1,2})?[Ee]\d{1,4})(?=[.\s]|$)")
# 날짜는 6자리(YYMMDD) 또는 8자리(YYYYMMDD — 19/20 세기 접두)
DATE_RE = re.compile(r"(?<=[.\s])((?:19|20)?\d{6})(?=[.\s]|$)")


def norm_date(token: str) -> str:
    """8자리 날짜(YYYYMMDD)의 세기 접두를 지워 6자리(YYMMDD)로 통일한다."""
    return token[2:] if len(token) == 8 else token

# 제거 대상 잡태그(dot 경계 토큰). 긴 패턴을 앞에 둬야 부분 매치를 막는다.
_JUNK = (
    "SDTV|HDTV|WEB-DL|WEBRip|WEB|HDRip|REPACK|PROPER|"
    "AAC2\\.0|AAC|AC3|DDP?(?:2\\.0|5\\.1)|"
    "SBS|KBS2|KBS|MBC|JTBC|TVN|MBN|OCN|ENA|MNET|TVING|WAVVE|VIU|CPNG|CP"
)
JUNK_RE = re.compile(r"\.(?:" + _JUNK + r")(?=\.|$)", re.IGNORECASE)

# 부제/기술영역 경계 판정용 핵심 기술 토큰(날짜·해상도·코덱)
CORE_TECH_RE = re.compile(
    r"\.((?:19|20)?\d{6}|\d{3,4}[PpIi]|[Xx]26[45]|H\.?26[45]|HEVC|AVC"
    r"|MPEG(?:-?2)?|XVID|DIVX)"
    r"(?=\.|$)",
    re.IGNORECASE,
)

# 기술영역 화이트리스트: 날짜/해상도/코덱/한글 토큰만 통과(그 외는 전부 제거)
KEEP_RE = re.compile(
    r"\.((?:(?:19|20)?\d{6}|\d{3,4}[PpIi]|[Xx]26[45]|H\.?26[45]|HEVC|AVC"
    r"|MPEG(?:-?2)?|XVID|DIVX)"
    r"(?=\.|$)"
    r"|[^.]*[가-힣][^.]*)",
    re.IGNORECASE,
)


@dataclass
class Result:
    changed: int = 0
    kept: int = 0
    unparsed: int = 0
    failed: int = 0

    def merge(self, other: "Result") -> None:
        self.changed += other.changed
        self.kept += other.kept
        self.unparsed += other.unparsed
        self.failed += other.failed


def clean_tail(tail: str) -> str:
    """기준점(에피소드/날짜) 뒤 문자열을 '[날짜].[한글부제].해상도[.코덱]' 로 정리한다."""
    d = JUNK_RE.sub("", "." + tail)
    d = re.sub(r"\.{2,}", ".", d).rstrip(".")
    if d in ("", "."):
        return ""
    m = CORE_TECH_RE.search(d)
    if m is None:
        extra, rest = d.strip("."), ""
    else:
        extra, rest = d[: m.start()].strip("."), d[m.start():].strip(".")
    # 부제 판정(토큰 단위): 한글 포함 토큰만 보존(직접 수정한 게스트명),
    # 영문 토큰은 제거(로마자 병기 게스트명·에피소드 부제 모두 해당)
    if extra:
        extra = ".".join(t for t in extra.split(".") if HANGUL_RE.search(t))
    # 기술영역 화이트리스트 강제: 날짜/해상도/코덱/한글 토큰만 남기고
    # 고정 순서로 재조립한다. 코덱은 x26x 로 통일해 해상도 바로 뒤에 붙이고,
    # 낀 한글 게스트명(...1080p.이현우(배우).x264)은 해상도 앞으로 옮긴다.
    dates: list[str] = []
    hangul: list[str] = []
    res = codec = ""
    for k in KEEP_RE.finditer("." + rest):
        t = k.group(1)
        if HANGUL_RE.search(t):
            hangul.append(t)
        elif t.isdigit():
            dates.append(norm_date(t))
        elif t[-1] in "PpIi":
            res = res or t[:-1] + t[-1].lower()
        elif not codec:
            u = t.upper().replace(".", "").replace("-", "")
            if u.startswith("MPEG"):
                # 변환 대상 아님 — 원표기 보존(MPEG-2/MPEG2 → MPEG2, MPEG → MPEG)
                codec = "MPEG2" if u.endswith("2") else "MPEG"
            elif u == "XVID":
                codec = "XviD"       # 구형 릴 코덱 — 보존
            elif u == "DIVX":
                codec = "DivX"       # 구형 릴 코덱 — 보존
            elif u in ("H265", "HEVC", "X265"):
                codec = "x265"
            else:
                codec = "x264"
    if res and not codec:
        codec = "x264"  # 무표기 릴은 x264 (x265 는 반드시 표기됨)
    parts = [*dates, extra, *hangul, res, codec]
    return ".".join(p for p in parts if p)


def transform(stem: str, strip_season: bool = False) -> str | None:
    """스템(확장자 제외)을 선호 형식으로 변환한다. 기준점이 없으면 None.

    strip_season=True 면 에피소드 마커의 시즌 접두를 제거한다(S02E03 → E03).
    """
    group = None
    core = stem
    m = GROUP_RE.search(stem)
    if m:
        cand, prefix = m.group(1), stem[: m.start()]
        # 오인 방지: 'WEB-DL...' 끝맺음, 또는 후보에 기술 토큰(날짜/해상도/코덱)이
        # 섞인 경우(예: 'Eun-Ji.1080p.x264')는 그룹이 아니다
        bogus = (cand.upper().startswith("DL") and prefix.upper().endswith("WEB")) \
            or CORE_TECH_RE.search("." + cand) is not None
        if not bogus:
            group, core = cand, prefix

    dotted = "." + core
    ep = EP_RE.search(dotted)
    if ep:
        marker = ep.group(1).upper()
        if strip_season:
            marker = re.sub(r"^S\d+", "", marker)
        # 기준점 앞 구분자(공백 포함)는 dot 로 정규화: "제목 E01" → "제목.E01"
        head = (dotted[: ep.start()].rstrip(". ") + "." + marker).strip(".")
        tail = dotted[ep.end():].lstrip(". ")
    else:
        date = DATE_RE.search(dotted)
        if date is None:
            return None
        head = (dotted[: date.start()].rstrip(". ")
                + "." + norm_date(date.group(1))).strip(".")
        tail = dotted[date.end():].lstrip(". ")

    cleaned = clean_tail(tail) if tail else ""
    new = head + ("." + cleaned if cleaned else "")
    if group:
        new += "-" + group
    return new


def process_dir(root: Path, apply: bool) -> Result:
    res = Result()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS)
    print(f"[대상] {root} — 파일 {len(files)}개")
    planned: set[str] = set()  # 이번 실행에서 이미 예약된 대상명(계획 내부 충돌 감지)
    for p in files:
        new_stem = transform(p.stem)
        if new_stem is None:
            print(f"  [패턴불일치] {p.name}")
            res.unparsed += 1
            continue
        if new_stem == p.stem:
            res.kept += 1
            continue
        target = p.with_name(new_stem + p.suffix)
        occupied = target.exists() and target.name.lower() != p.name.lower()
        if occupied or str(target).lower() in planned:
            print(f"  [충돌] {p.name} → {target.name}: 대상 파일이 이미 존재")
            res.failed += 1
            continue
        planned.add(str(target).lower())
        tag = "변경" if apply else "변경예정"
        print(f"  [{tag}] {p.name}\n      → {target.name}")
        if apply:
            try:
                p.rename(target)
            except OSError as e:
                print(f"  [실패] {p.name}: {e}")
                res.failed += 1
                continue
        res.changed += 1
    return res


def main() -> int:
    args = sys.argv[1:]
    apply = "--apply" in args
    roots = [a for a in args if a != "--apply"]
    if not roots:
        print(f'사용법: python {Path(sys.argv[0]).name} "<폴더경로>" [<폴더경로2> ...] [--apply]')
        return 2

    total = Result()
    for arg in roots:
        root = Path(arg)
        if not root.is_dir():
            print(f"[오류] 폴더가 아니거나 접근 불가: {arg}")
            total.failed += 1
            continue
        total.merge(process_dir(root, apply))

    mode = "변경" if apply else "변경예정(미리보기)"
    print(f"\n[요약] {mode} {total.changed} / 유지 {total.kept}"
          f" / 패턴불일치 {total.unparsed} / 실패 {total.failed}")
    if not apply and total.changed:
        print("실제 변경하려면 --apply 를 붙여 다시 실행한다.")
    return 0 if total.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
