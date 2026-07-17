# -*- coding: utf-8 -*-
"""rename_gui.py — rename.py 의 GUI 프런트엔드(PySide6).

사용 흐름:
    1. 경로 입력(직접 입력 또는 찾아보기) 후 [미리보기] — 변경 대상을 로그에 표시.
       요약에 mkv변환 대상(비-MKV 영상/변환 가능 수) 현황도 함께 표기한다.
    2. [적용] — 미리보기에서 확정된 목록을 실제로 반영.
    3. 경로를 수정하면 기존 미리보기 결과는 무효화되어 [적용]이 비활성화되고,
       다시 [미리보기]를 눌러야 한다(새 경로 기준으로 재스캔).
    4. [시즌제거] — 토글. 켜면 최종 이름에서 시즌 접두를 제거(S02E03 → E03)하고
       즉시 재미리보기하며 버튼이 [시즌제거취소]로 바뀐다. 취소하면 시즌 복구.
    5. [자막변환] — 경로 아래 모든 .smi 를 .srt 로 변환한다(smi2srt 규칙).
       [자막변환후원본제거] 체크 시 변환에 성공한 .smi 와, srt 가 이미 있어
       스킵된 .smi 를 삭제한다(변환 실패분만 남긴다).
    6. [합치기] — 라디오로 선택한 모드를 경로 아래 모든 대상에 실행한다:
       [mkv변환] 비-MKV 영상(.mp4/.avi/.ts/.wmv/.mov)을 조건 없이 MKV 컨테이너로
         변환한다(트랙 전체 보존, 검증 후 원본 컨테이너 제거). 이하 모드는 .mkv 대상:
       [기본해제+ko추가] 기존 자막 기본 플래그 전부 해제 + 같은 이름 .srt 를
         ko 기본 자막으로 추가. [자막제거+ko추가] 기존 자막 전부 제거 + ko 추가 —
         이 모드만 같은 이름 .srt 가 있는 비-MKV 영상도 대상에 포함해 .mkv 로
         변환하며 합친다(mkv변환+합치기 겸용, 검증 후 원본 컨테이너 제거).
       [자막제거] 자막 트랙만 제거(del_sup.py 와 동일, 용량 절감 보고).
       공통: 임시 파일로 만든 뒤 트랙 구성을 검증하고 원본을 원자 교체한다.
       목표 상태가 이미 달성된 파일은 스킵한다(재실행 안전). .srt 원본은 남긴다.
       mkvmerge.exe 위치는 .env 의 PATH_MKV_TOOLS 를 따른다(미설정 시 스크립트 폴더).
       [합치기 테스트] — 첫 대상 파일 1개만 같은 모드로 처리해 <이름>.muxtest.mkv 로
       저장한다(원본 유지). 결과 확인 후 이상 없으면 [합치기]로 전체 진행.
       [동시 작업](1~4) — 전체 실행 시 병렬로 처리할 파일 수. 출력 검증은
       rc=0(무경고)·크기 정상이면 생략해 대량 배치의 프로브 비용을 없앤다
       (경고·크기 이상·플래그 조작 케이스는 풀 검증 유지).
       [남길 자막](ko/en/und 체크) — 자막제거+ko추가/자막제거 모드에서만 동작.
       선택 언어의 자막은 보존하고 그 외 전부 제거한다. ko 기본트랙 규칙:
       ko 자막이 기본이 아니면 전체 기본 해제 후 ko(2개면 첫번째)를 기본으로,
       ko 가 3개 이상이면 해당 파일은 알리고 진행하지 않는다(보류).
    7. 옵션(최상위/시즌제거/원본제거 체크/합치기 모드)·마지막 사용 경로·
       창 위치/크기는 QSettings 로 영속되어 재실행 시 복원된다.
    8. [중지] 버튼(실행 중에만 활성) 또는 창 닫기(X)는 작업을 즉시 중단한다:
       실행 중 mkvmerge 를 종료하고 임시 파일을 삭제하며 원본은 보존한다.
    9. [자막변환]/[합치기]/[합치기 테스트] 완료 시 알림 창을 띄운다. 창이
       전면이 아니면 임시 최상위로 올린 뒤 띄우고 알림 종료 시 원상 복구한다.
       [중지]로 끝났거나 창을 닫으며 중단된 경우는 띄우지 않는다.

파일명 변환 규칙은 rename.py 의 transform() 이 단일출처다.
"""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from del_sup import format_elapsed, format_size
from rename import EXTS, transform
from smi2srt import convert as smi_convert

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
MKVMERGE = Path(_ENV.get("PATH_MKV_TOOLS") or _SCRIPT_DIR) / "mkvmerge.exe"
MUX_TMP_SUFFIX = ".mux.tmp.mkv"
MUX_TEST_SUFFIX = ".muxtest.mkv"  # 합치기 테스트 산출물 — 모든 스캔에서 제외
REMUX_EXTS = {".mp4", ".avi", ".ts", ".wmv", ".mov"}  # mkv변환 대상(비-MKV 영상)
# MKVToolNix GUI 가 쌓는 파일 식별 캐시 — 무한 증식하므로 작업 시작 시 비운다
IDENT_CACHE_DIR = MKVMERGE.parent / "cache" / "fileIdentifier"


def prune_identifier_cache() -> int:
    """식별 캐시 파일을 삭제하고 개수를 반환한다(재생성되는 캐시라 무해)."""
    n = 0
    if IDENT_CACHE_DIR.is_dir():
        for f in IDENT_CACHE_DIR.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass  # 사용 중 파일은 다음 기회에 정리
    return n


def probe(mkv: Path) -> dict | None:
    """mkvmerge -J 로 컨테이너 정보를 반환한다. 검사 실패 시 None."""
    proc = subprocess.run(
        [str(MKVMERGE), "-J", str(mkv)],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode == 2:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def is_ko(track: dict) -> bool:
    p = track.get("properties", {})
    return p.get("language") in ("ko", "kor") or \
        str(p.get("language_ietf", "")).startswith("ko")


def sub_lang(track: dict) -> str:
    """자막 트랙 언어를 2자 코드로 정규화한다(kor→ko, eng→en, 미지정→und)."""
    p = track.get("properties", {})
    ietf = str(p.get("language_ietf") or "").split("-")[0].lower()
    if ietf:
        return {"kor": "ko", "eng": "en"}.get(ietf, ietf)
    lang = str(p.get("language") or "und").lower()
    return {"kor": "ko", "eng": "en"}.get(lang, lang)


class Worker(QThread):
    """스캔/적용을 백그라운드에서 수행한다(UNC 대량 스캔 시 UI 멈춤 방지)."""

    line = Signal(str)
    done = Signal(object)  # preview 모드: list[(Path, Path)], apply 모드: None

    def __init__(self, mode: str, root: Path | None = None,
                 plan: list[tuple[Path, Path]] | None = None,
                 strip_season: bool = False, delete_smi: bool = False,
                 mux_strip: bool = False, mux_test: bool = False,
                 keep_langs: tuple[str, ...] = (), jobs: int = 1) -> None:
        super().__init__()
        self.mode = mode
        self.root = root
        self.plan = plan or []
        self.strip_season = strip_season
        self.delete_smi = delete_smi
        self.mux_strip = mux_strip  # True=자막제거+ko추가, False=기본해제+ko추가
        self.mux_test = mux_test    # True=첫 대상 1개만 muxtest 출력, 원본 유지
        self.keep_langs = keep_langs  # 남길 자막 언어(자막제거 계열 모드에서만)
        self.jobs = max(1, jobs)      # 동시 파일 작업 수(합치기 계열 전체 실행)
        self._cancel = False
        self._procs: set[subprocess.Popen] = set()  # 실행 중 mkvmerge 핸들
        self._lock = threading.Lock()
        self._prefetch: dict[str, Future] = {}  # 다음 파일 프로브 선읽기 결과

    def stop(self) -> None:
        """중단 요청: 남은 파일은 건너뛰고 실행 중 mkvmerge 는 즉시 종료한다."""
        with self._lock:
            self._cancel = True
            for p in self._procs:
                p.kill()

    def _probe_cached(self, path: Path) -> dict | None:
        """선읽기된 프로브가 있으면 사용하고 없으면 직접 프로브한다."""
        fut = self._prefetch.pop(str(path), None)
        if fut is not None:
            try:
                return fut.result()
            except Exception:  # noqa: BLE001 — 선읽기 실패는 직접 프로브로 폴백
                pass
        return probe(path)

    def _run_mkvmerge(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """중단 가능하도록 프로세스 핸들을 추적하며 mkvmerge 를 실행한다."""
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        with self._lock:
            if self._cancel:
                proc.kill()
            self._procs.add(proc)
        out, err = proc.communicate()
        with self._lock:
            self._procs.discard(proc)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

    def run(self) -> None:
        try:
            n = prune_identifier_cache()
            if n:
                self.line.emit(f"[캐시정리] fileIdentifier {n}개 삭제")
            if self.mode == "preview":
                self.done.emit(self._preview())
            elif self.mode == "smi":
                self._smi()
                self.done.emit(None)
            elif self.mode == "mux":
                self._mux()
                self.done.emit(None)
            elif self.mode == "delsub":
                self._delsub()
                self.done.emit(None)
            elif self.mode == "remux":
                self._remux()
                self.done.emit(None)
            else:
                self._apply()
                self.done.emit(None)
        except OSError as e:
            self.line.emit(f"[오류] {e}")
            self.done.emit([] if self.mode == "preview" else None)

    def _preview(self) -> list[tuple[Path, Path]]:
        assert self.root is not None  # preview 모드는 root 필수(타입 내로잉)
        plan: list[tuple[Path, Path]] = []
        planned: set[str] = set()  # 계획 내부 충돌 감지(시즌제거 시 회차 겹침 대비)
        kept = unparsed = conflict = 0
        files = sorted(
            p for p in self.root.rglob("*")
            if p.is_file() and p.suffix.lower() in EXTS
            and not p.name.endswith(MUX_TEST_SUFFIX)
        )
        mode = " (시즌제거)" if self.strip_season else ""
        self.line.emit(f"[대상] {self.root} — 파일 {len(files)}개{mode}")
        for p in files:
            if self._cancel:
                break
            new_stem = transform(p.stem, self.strip_season)
            if new_stem is None:
                unparsed += 1
                self.line.emit(f"  [패턴불일치] {p.name}")
                continue
            if new_stem == p.stem:
                kept += 1
                continue
            target = p.with_name(new_stem + p.suffix)
            occupied = target.exists() and target.name.lower() != p.name.lower()
            if occupied or str(target).lower() in planned:
                conflict += 1
                self.line.emit(f"  [충돌] {p.name} → {target.name}: 대상 파일이 이미 존재")
                continue
            planned.add(str(target).lower())
            plan.append((p, target))
            self.line.emit(f"  [변경예정] {p.name}\n      → {target.name}")
        self.line.emit(
            f"\n[요약] 변경예정 {len(plan)} / 유지 {kept}"
            f" / 패턴불일치 {unparsed} / 충돌 {conflict}"
        )
        # mkv변환 대상 현황도 함께 보고(미리보기가 정찰 단계 — 합치기 전 판단용)
        remux = [p for p in files if p.suffix.lower() in REMUX_EXTS]
        if remux:
            n_conv = sum(1 for p in remux if not p.with_suffix(".mkv").exists())
            self.line.emit(f"[mkv변환 대상] 비-MKV 영상 {len(remux)}개"
                           f" / 변환 가능 {n_conv}개(나머지는 mkv 이미 존재)")
        return plan

    def _smi(self) -> None:
        assert self.root is not None  # smi 모드는 root 필수(타입 내로잉)
        done = skipped = failed = deleted = 0
        files = sorted(self.root.rglob("*.smi"))
        self.line.emit(f"[자막변환] {self.root} — SMI {len(files)}개")
        for f in files:
            if self._cancel:
                break
            try:
                state, cues = smi_convert(f, overwrite=False)
            except OSError as e:
                self.line.emit(f"  [실패] {f.name}: {e}")
                failed += 1
                continue
            if state == "done":
                done += 1
                msg = f"  [완료] {f.name} → {f.with_suffix('.srt').name} (큐 {cues}개)"
                if self.delete_smi:
                    try:
                        f.unlink()
                        deleted += 1
                        msg += " — 원본 제거"
                    except OSError as e:
                        msg += f" — 원본 제거 실패: {e}"
                self.line.emit(msg)
            elif state == "skip":
                skipped += 1
                if self.delete_smi:  # srt 가 이미 있으면 smi 는 잉여 — 제거
                    try:
                        f.unlink()
                        deleted += 1
                        self.line.emit(f"  [원본제거] {f.name} (srt 이미 존재)")
                    except OSError as e:
                        self.line.emit(f"  [원본제거 실패] {f.name}: {e}")
            else:
                failed += 1
                self.line.emit(f"  [파싱실패] {f.name}: 유효한 자막 큐 없음")
        tail = f" / 원본제거 {deleted}" if self.delete_smi else ""
        self.line.emit(f"\n[요약] 변환 {done} / 스킵(srt존재) {skipped} / 실패 {failed}{tail}")

    def _mux_one(self, src: Path) -> tuple[str, list[str]]:
        """단일 영상에 ko 자막을 합친다. (상태, 로그라인) 반환 — 병렬 실행 안전.

        자막제거+ko추가 모드는 비-MKV 영상(REMUX_EXTS)도 받아 .mkv 로 변환하며 합친다.
        """
        lines: list[str] = []
        if self._cancel:
            return "cancel", lines
        srt = src.with_suffix(".srt")
        if not srt.exists():
            if not self.mux_test:
                lines.append(f"  [스킵] {src.name}: 같은 이름의 .srt 없음")
            return "skip", lines
        try:
            state = self._mux_body(src, srt, lines)
        except OSError as e:
            lines.append(f"  [실패] {src.name}: {e}")
            state = "fail"
        return state, lines

    def _mux_body(self, src: Path, srt: Path, lines: list[str]) -> str:
        t0 = time.monotonic()
        remux = src.suffix.lower() != ".mkv"  # 비-MKV 소스: 변환+합치기 동시 수행
        dst = src.with_suffix(".mkv")
        if remux and dst.exists():
            if not self.mux_test:
                lines.append(f"  [스킵] {src.name}: {dst.name} 이미 존재")
            return "skip"
        info = self._probe_cached(src)
        if info is None:
            lines.append(f"  [검사실패] {src.name}")
            return "fail"
        tracks = info.get("tracks", [])
        subs = [t for t in tracks if t.get("type") == "subtitles"]
        n_video = sum(1 for t in tracks if t.get("type") == "video")
        n_audio = sum(1 for t in tracks if t.get("type") == "audio")
        ko_default = any(
            is_ko(t) and t.get("properties", {}).get("default_track") for t in subs
        )
        # 남길 자막(자막제거+ko추가 모드에서만): 선택 언어 자막은 보존
        keep: list[dict] = []
        if self.mux_strip and self.keep_langs:
            keep = [t for t in subs if sub_lang(t) in self.keep_langs]
            ko_kept = [t for t in keep if sub_lang(t) == "ko"]
            if len(ko_kept) >= 3:
                lines.append(f"  [보류] {src.name}: ko 자막 {len(ko_kept)}개 —"
                             f" 수동 확인 필요, 진행하지 않음")
                return "skip"
        # 목표 상태 달성 시 스킵(비-MKV 는 컨테이너 변환이 남아 항상 진행):
        # 옵션1 은 ko 기본 존재, 옵션2 는 ko 기본 단독
        # (남길 자막 지정 시: ko 기본 존재 + 모든 자막이 남길 언어(∪ko) 안)
        if ko_default and not remux:
            if not self.mux_strip:
                lines.append(f"  [스킵] {src.name}: 이미 ko 기본 자막 있음")
                return "skip"
            allowed = set(self.keep_langs) | {"ko"}
            if (self.keep_langs and all(sub_lang(t) in allowed for t in subs)) \
                    or (not self.keep_langs and len(subs) == 1):
                lines.append(f"  [스킵] {src.name}: 이미 ko 기본 자막 있음")
                return "skip"

        tmp = src.with_name(src.stem + MUX_TMP_SUFFIX)
        if tmp.exists():
            tmp.unlink()
        cmd = [str(MKVMERGE), "-o", str(tmp)]
        if self.mux_strip:
            if keep:
                cmd += ["--subtitle-tracks", ",".join(str(t["id"]) for t in keep)]
                for t in keep:  # 보존 자막은 전부 기본 해제(기본은 새 ko srt)
                    cmd += ["--default-track-flag", f"{t['id']}:0"]
            else:
                cmd += ["--no-subtitles"]
        else:
            for t in subs:
                cmd += ["--default-track-flag", f"{t['id']}:0"]
        cmd += [str(src), "--language", "0:ko", "--default-track-flag", "0:1", str(srt)]
        proc = self._run_mkvmerge(cmd)
        if self._cancel:
            if tmp.exists():
                tmp.unlink()
            lines.append(f"  [중단] {src.name}: 원본 보존, 임시 파일 삭제")
            return "cancel"
        if proc.returncode == 2 or not tmp.exists():
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            lines.append(f"  [합치기실패] {src.name}: {err}")
            if tmp.exists():
                tmp.unlink()
            return "fail"

        # 출력 검증은 의심 경로에서만: rc=1(경고) 또는 출력 크기 이상 시 풀 검증.
        # rc=0 + 크기 정상은 신뢰한다(수백~수천 파일 배치의 프로브 비용 절감).
        if proc.returncode != 0 or tmp.stat().st_size < src.stat().st_size * 0.5:
            out = probe(tmp)
            ok = False
            if out is not None:
                ot = out.get("tracks", [])
                osubs = [t for t in ot if t.get("type") == "subtitles"]
                ko_def = [t for t in osubs
                          if is_ko(t) and t.get("properties", {}).get("default_track")]
                other_def = [t for t in osubs
                             if not is_ko(t)
                             and t.get("properties", {}).get("default_track")]
                expected_subs = (len(keep) + 1) if self.mux_strip else len(subs) + 1
                ok = (
                    sum(1 for t in ot if t.get("type") == "video") == n_video
                    and sum(1 for t in ot if t.get("type") == "audio") == n_audio
                    and len(osubs) == expected_subs
                    and len(ko_def) == 1 and not other_def
                )
            if not ok:
                lines.append(f"  [검증실패] {src.name}: 원본 보존, 임시 파일 삭제")
                tmp.unlink()
                return "fail"

        if self.mux_strip:
            if keep:
                detail = f"자막 {len(subs) - len(keep)}개 제거·{len(keep)}개 유지"
            else:
                detail = "기존 자막 제거" if subs else "기존 자막 없음"
        else:
            detail = f"기존 자막 {len(subs)}개 기본해제"
        elapsed = format_elapsed(time.monotonic() - t0)
        if self.mux_test:
            dst_test = src.with_name(src.stem + MUX_TEST_SUFFIX)
            if dst_test.exists():
                dst_test.unlink()
            tmp.replace(dst_test)
            lines.append(f"  [테스트완료] {src.name}: {detail}, ko 자막 추가(기본)"
                         f" ({elapsed})\n      → {dst_test.name} (원본 유지)")
        elif remux:
            tmp.replace(dst)
            try:
                src.unlink()  # 검증 통과한 .mkv 가 자리잡은 뒤 원본 컨테이너 제거
            except OSError as e:
                lines.append(f"  [경고] {src.name}: 원본 제거 실패 — {e}")
            lines.append(f"  [완료] {src.name} → {dst.name}: {detail},"
                         f" ko 자막 추가(기본) ({elapsed})")
        else:
            tmp.replace(src)
            lines.append(f"  [완료] {src.name}: {detail}, ko 자막 추가(기본) ({elapsed})")
        return "done"

    def _iter_results(self, files, one):
        """파일별 작업을 실행하고 결과를 파일 순서대로 낸다.

        테스트 모드는 순차 실행하며 첫 실제 대상(스킵 아님)에서 중단한다.
        그 외에는 동시 작업 수(jobs)만큼 병렬 실행한다(로그 순서는 유지).
        """
        if self.mux_test or self.jobs <= 1:
            # 순차 실행: 다음 파일의 프로브를 현재 합치기와 병렬로 선읽기해
            # 파일 사이의 죽은 시간(NAS 위 MP4 인덱스 읽기 수 초~십수 초)을 없앤다
            with ThreadPoolExecutor(max_workers=1) as pool:
                for i, f in enumerate(files):
                    if self._cancel:
                        break
                    if not self.mux_test and i + 1 < len(files):
                        nxt = files[i + 1]
                        self._prefetch[str(nxt)] = pool.submit(probe, nxt)
                    res = one(f)
                    yield res
                    if self.mux_test and res[0] != "skip":
                        break
        else:
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futs = [pool.submit(one, f) for f in files]
                for fut in futs:
                    yield fut.result()

    def _mux(self) -> None:
        assert self.root is not None  # mux 모드는 root 필수(타입 내로잉)
        if not MKVMERGE.exists():
            self.line.emit(f"[오류] mkvmerge.exe 를 찾을 수 없음: {MKVMERGE}")
            return
        done = skipped = failed = 0
        # 자막제거+ko추가는 비-MKV 영상도 대상(결과가 .mkv 라 mkv변환+합치기 겸용)
        exts = {".mkv"} | (REMUX_EXTS if self.mux_strip else set())
        files = sorted(p for p in self.root.rglob("*")
                       if p.is_file() and p.suffix.lower() in exts
                       and not p.name.endswith(MUX_TMP_SUFFIX)
                       and not p.name.endswith(MUX_TEST_SUFFIX))
        mode = "자막제거+ko추가" if self.mux_strip else "기본해제+ko추가"
        if self.mux_strip and self.keep_langs:
            mode += f"(남길: {','.join(self.keep_langs)})"
        tag = " 테스트(1개만, 원본 유지)" if self.mux_test else ""
        jobs = f" (동시 {self.jobs})" if self.jobs > 1 and not self.mux_test else ""
        started = time.monotonic()
        n_conv = sum(1 for p in files if p.suffix.lower() != ".mkv")
        head = f"MKV {len(files) - n_conv}개"
        if self.mux_strip:
            head += f" + 비-MKV {n_conv}개(mkv 변환 겸)"
        self.line.emit(f"[자막합치기:{mode}{tag}] {self.root} — {head}{jobs}")
        # 같은 스템의 비-MKV 둘(A.mp4+A.ts)이 같은 A.mkv 를 두고 경쟁하지 않도록
        # 배치 내 변환 대상명이 중복되면 뒤의 것을 충돌로 제외한다
        run: list[Path] = []
        seen: set[str] = set()
        for p in files:
            if p.suffix.lower() == ".mkv":
                run.append(p)
                continue
            key = str(p.with_suffix(".mkv")).lower()
            if key in seen:
                self.line.emit(f"  [충돌] {p.name}: 변환 대상명이 배치 내 중복")
                failed += 1
                continue
            seen.add(key)
            run.append(p)
        for state, lines in self._iter_results(run, self._mux_one):
            for ln in lines:
                self.line.emit(ln)
            if state == "done":
                done += 1
            elif state == "fail":
                failed += 1
            elif state == "cancel":
                pass  # 미처리 — 아래 [중단됨] 안내에 포함
            else:
                skipped += 1
        self.line.emit(f"\n[요약] 완료 {done} / 스킵 {skipped} / 실패 {failed}"
                       f" / 소요 {format_elapsed(time.monotonic() - started)}")
        if self._cancel:
            rest = len(files) - done - skipped - failed
            self.line.emit(f"[중단됨] 남은 {rest}개는 처리하지 않음(원본 보존)")
        if self.mux_test and done:
            self.line.emit("결과(.muxtest.mkv) 확인 후 이상 없으면 [합치기]로 전체 진행.")

    def _delsub_one(self, mkv: Path) -> tuple[str, list[str], int, int]:
        """단일 mkv 자막 제거/정리. (상태, 로그라인, 원본크기, 출력크기) 반환."""
        lines: list[str] = []
        if self._cancel:
            return "cancel", lines, 0, 0
        try:
            state, src, out = self._delsub_body(mkv, lines)
        except OSError as e:
            lines.append(f"  [실패] {mkv.name}: {e}")
            state, src, out = "fail", 0, 0
        return state, lines, src, out

    def _delsub_body(self, mkv: Path, lines: list[str]) -> tuple[str, int, int]:
        t0 = time.monotonic()
        info = self._probe_cached(mkv)
        if info is None:
            lines.append(f"  [검사실패] {mkv.name}")
            return "fail", 0, 0
        tracks = info.get("tracks", [])
        subs = [t for t in tracks if t.get("type") == "subtitles"]
        n_video = sum(1 for t in tracks if t.get("type") == "video")
        n_audio = sum(1 for t in tracks if t.get("type") == "audio")
        if not subs:
            if not self.mux_test:
                lines.append(f"  [스킵] {mkv.name}: 자막 없음")
            return "skip", 0, 0
        # 남길 자막: 선택 언어 보존 + ko 기본트랙 규칙(2개→첫번째, 3개↑→보류)
        keep = ([t for t in subs if sub_lang(t) in self.keep_langs]
                if self.keep_langs else [])
        ko_kept = [t for t in keep if sub_lang(t) == "ko"]
        if len(ko_kept) >= 3:
            lines.append(f"  [보류] {mkv.name}: ko 자막 {len(ko_kept)}개 —"
                         f" 수동 확인 필요, 진행하지 않음")
            return "hold", 0, 0
        chosen = ko_kept[0] if ko_kept else None
        if len(keep) == len(subs):  # 제거할 자막 없음 — 기본 플래그만 점검
            defaults = [t for t in subs
                        if t.get("properties", {}).get("default_track")]
            if chosen is None or defaults == [chosen]:
                if not self.mux_test:
                    lines.append(f"  [스킵] {mkv.name}: 이미 목표 상태")
                return "skip", 0, 0
        tmp = mkv.with_name(mkv.stem + MUX_TMP_SUFFIX)
        if tmp.exists():
            tmp.unlink()
        cmd = [str(MKVMERGE), "-o", str(tmp)]
        if keep:
            cmd += ["--subtitle-tracks", ",".join(str(t["id"]) for t in keep)]
            if chosen is not None:
                for t in keep:
                    cmd += ["--default-track-flag",
                            f"{t['id']}:{1 if t is chosen else 0}"]
        else:
            cmd += ["--no-subtitles"]
        cmd.append(str(mkv))
        proc = self._run_mkvmerge(cmd)
        if self._cancel:
            if tmp.exists():
                tmp.unlink()
            lines.append(f"  [중단] {mkv.name}: 원본 보존, 임시 파일 삭제")
            return "cancel", 0, 0
        if proc.returncode == 2 or not tmp.exists():
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            lines.append(f"  [제거실패] {mkv.name}: {err}")
            if tmp.exists():
                tmp.unlink()
            return "fail", 0, 0
        # 검증은 의심 경로에서만: rc=1(경고)·크기 이상·플래그를 조작한 keep 케이스
        if proc.returncode != 0 or keep \
                or tmp.stat().st_size < mkv.stat().st_size * 0.5:
            out = probe(tmp)
            ok = False
            if out is not None:
                ot = out.get("tracks", [])
                osubs = [t for t in ot if t.get("type") == "subtitles"]
                flags_ok = True
                if chosen is not None:
                    defs = [t for t in osubs
                            if t.get("properties", {}).get("default_track")]
                    flags_ok = len(defs) == 1 and sub_lang(defs[0]) == "ko"
                ok = (
                    len(osubs) == len(keep)
                    and sum(1 for t in ot if t.get("type") == "video") == n_video
                    and sum(1 for t in ot if t.get("type") == "audio") == n_audio
                    and flags_ok
                )
            if not ok:
                lines.append(f"  [검증실패] {mkv.name}: 원본 보존, 임시 파일 삭제")
                tmp.unlink()
                return "fail", 0, 0
        detail = f"자막 {len(subs) - len(keep)}개 제거"
        if keep:
            detail += f"·{len(keep)}개 유지"
            if chosen is not None:
                detail += "(기본: ko)"
        elapsed = format_elapsed(time.monotonic() - t0)
        if self.mux_test:
            dst_path = mkv.with_name(mkv.stem + MUX_TEST_SUFFIX)
            if dst_path.exists():
                dst_path.unlink()
            tmp.replace(dst_path)
            lines.append(f"  [테스트완료] {mkv.name}: {detail} ({elapsed})\n"
                         f"      → {dst_path.name} (원본 유지)")
            return "done", 0, 0
        src, dst = mkv.stat().st_size, tmp.stat().st_size
        tmp.replace(mkv)
        lines.append(f"  [완료] {mkv.name}: {detail}"
                     f" ({format_size(src)} → {format_size(dst)}, {elapsed})")
        return "done", src, dst

    def _delsub(self) -> None:
        """모든 mkv 의 자막 트랙 제거/정리(검증 후 원자 교체)."""
        assert self.root is not None  # delsub 모드는 root 필수(타입 내로잉)
        if not MKVMERGE.exists():
            self.line.emit(f"[오류] mkvmerge.exe 를 찾을 수 없음: {MKVMERGE}")
            return
        done = skipped = failed = 0
        src_bytes = out_bytes = 0
        files = sorted(p for p in self.root.rglob("*.mkv")
                       if not p.name.endswith(MUX_TMP_SUFFIX)
                       and not p.name.endswith(MUX_TEST_SUFFIX))
        tag = " 테스트(1개만, 원본 유지)" if self.mux_test else ""
        keeps = f"(남길: {','.join(self.keep_langs)})" if self.keep_langs else ""
        jobs = f" (동시 {self.jobs})" if self.jobs > 1 and not self.mux_test else ""
        started = time.monotonic()
        self.line.emit(f"[자막제거{keeps}{tag}] {self.root} — MKV {len(files)}개{jobs}")
        for state, lines, src, out in self._iter_results(files, self._delsub_one):
            for ln in lines:
                self.line.emit(ln)
            if state == "done":
                done += 1
                src_bytes += src
                out_bytes += out
            elif state == "fail":
                failed += 1
            elif state == "cancel":
                pass  # 미처리 — 아래 [중단됨] 안내에 포함
            else:  # skip / hold(보류)
                skipped += 1
        summary = f"\n[요약] 완료 {done} / 스킵 {skipped} / 실패 {failed}"
        if done and not self.mux_test:
            summary += (f" / 용량 {format_size(src_bytes)} → {format_size(out_bytes)}"
                        f" (절감 {format_size(src_bytes - out_bytes)})")
        summary += f" / 소요 {format_elapsed(time.monotonic() - started)}"
        self.line.emit(summary)
        if self._cancel:
            rest = len(files) - done - skipped - failed
            self.line.emit(f"[중단됨] 남은 {rest}개는 처리하지 않음(원본 보존)")
        if self.mux_test and done:
            self.line.emit("결과(.muxtest.mkv) 확인 후 이상 없으면 [합치기]로 전체 진행.")

    def _remux_one(self, src: Path) -> tuple[str, list[str]]:
        """단일 비-MKV 영상을 MKV 로 변환. (상태, 로그라인) 반환."""
        lines: list[str] = []
        if self._cancel:
            return "cancel", lines
        try:
            state = self._remux_body(src, lines)
        except OSError as e:
            lines.append(f"  [실패] {src.name}: {e}")
            state = "fail"
        return state, lines

    def _remux_body(self, src: Path, lines: list[str]) -> str:
        t0 = time.monotonic()
        dst = src.with_suffix(".mkv")
        if dst.exists():
            if not self.mux_test:
                lines.append(f"  [스킵] {src.name}: {dst.name} 이미 존재")
            return "skip"
        info = self._probe_cached(src)
        if info is None:
            lines.append(f"  [검사실패] {src.name}")
            return "fail"
        tracks = info.get("tracks", [])
        counts = {k: sum(1 for t in tracks if t.get("type") == k)
                  for k in ("video", "audio", "subtitles")}
        tmp = src.with_name(src.stem + MUX_TMP_SUFFIX)
        if tmp.exists():
            tmp.unlink()
        proc = self._run_mkvmerge([str(MKVMERGE), "-o", str(tmp), str(src)])
        if self._cancel:
            if tmp.exists():
                tmp.unlink()
            lines.append(f"  [중단] {src.name}: 원본 보존, 임시 파일 삭제")
            return "cancel"
        if proc.returncode == 2 or not tmp.exists():
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            lines.append(f"  [변환실패] {src.name}: {err}")
            if tmp.exists():
                tmp.unlink()
            return "fail"
        # 검증은 의심 경로에서만: rc=1(경고) 또는 출력 크기 이상 시 풀 검증
        if proc.returncode != 0 or tmp.stat().st_size < src.stat().st_size * 0.5:
            out = probe(tmp)
            ot = out.get("tracks", []) if out else None
            if ot is None or any(
                sum(1 for t in ot if t.get("type") == k) != counts[k]
                for k in counts
            ):
                lines.append(f"  [검증실패] {src.name}: 원본 보존, 임시 파일 삭제")
                tmp.unlink()
                return "fail"
        size = src.stat().st_size
        secs = max(time.monotonic() - t0, 0.1)
        # 실효 속도(MB/s)를 함께 기록 — 파일 크기가 달라도 실행 간 비교가 가능하다
        perf = f"{format_size(size)}, {format_elapsed(secs)}, {format_size(size / secs)}/s"
        if self.mux_test:
            dst_path = src.with_name(src.stem + MUX_TEST_SUFFIX)
            if dst_path.exists():
                dst_path.unlink()
            tmp.replace(dst_path)
            lines.append(f"  [테스트완료] {src.name} ({perf})\n"
                         f"      → {dst_path.name} (원본 유지)")
            return "done"
        tmp.replace(dst)
        try:
            src.unlink()  # 검증 통과한 .mkv 가 자리잡은 뒤 원본 컨테이너 제거
        except OSError as e:
            lines.append(f"  [경고] {src.name}: 원본 제거 실패 — {e}")
        lines.append(f"  [완료] {src.name} → {dst.name} ({perf})")
        return "done"

    def _remux(self) -> None:
        """비-MKV 영상 전체를 MKV 컨테이너로 변환한다(트랙 전체 보존)."""
        assert self.root is not None  # remux 모드는 root 필수(타입 내로잉)
        if not MKVMERGE.exists():
            self.line.emit(f"[오류] mkvmerge.exe 를 찾을 수 없음: {MKVMERGE}")
            return
        done = skipped = failed = 0
        files = sorted(p for p in self.root.rglob("*")
                       if p.is_file() and p.suffix.lower() in REMUX_EXTS)
        tag = " 테스트(1개만, 원본 유지)" if self.mux_test else ""
        jobs = f" (동시 {self.jobs})" if self.jobs > 1 and not self.mux_test else ""
        started = time.monotonic()
        self.line.emit(f"[mkv변환{tag}] {self.root} — 대상 {len(files)}개{jobs}")
        # 같은 스템의 입력 둘(A.mp4+A.avi)이 같은 A.mkv 를 두고 경쟁하지 않도록
        # 배치 내 대상명이 중복되면 뒤의 것을 충돌로 제외한다
        seen: set[str] = set()
        run: list[Path] = []
        for src in files:
            key = str(src.with_suffix(".mkv")).lower()
            if key in seen:
                self.line.emit(f"  [충돌] {src.name}: 변환 대상명이 배치 내 중복")
                failed += 1
                continue
            seen.add(key)
            run.append(src)
        # 실행 전 사전점검: 변환 가능 수를 먼저 보고한다(첫 파일 변환이 끝날
        # 때까지 로그가 없는 NAS 배치에서 헤더만 뜬 채 침묵하는 것을 방지)
        n_skip = sum(1 for s in run if s.with_suffix(".mkv").exists())
        self.line.emit(f"[사전점검] 변환 가능 {len(run) - n_skip}개"
                       f" / 이미 mkv 존재 {n_skip}개(스킵 예정)")
        for state, lines in self._iter_results(run, self._remux_one):
            for ln in lines:
                self.line.emit(ln)
            if state == "done":
                done += 1
            elif state == "fail":
                failed += 1
            elif state == "cancel":
                pass  # 미처리 — 아래 [중단됨] 안내에 포함
            else:
                skipped += 1
        self.line.emit(f"\n[요약] 완료 {done} / 스킵 {skipped} / 실패 {failed}"
                       f" / 소요 {format_elapsed(time.monotonic() - started)}")
        if self._cancel:
            rest = len(files) - done - skipped - failed
            self.line.emit(f"[중단됨] 남은 {rest}개는 처리하지 않음(원본 보존)")
        if self.mux_test and done:
            self.line.emit("결과(.muxtest.mkv) 확인 후 이상 없으면 [합치기]로 전체 진행.")

    def _apply(self) -> None:
        ok = fail = 0
        for src, dst in self.plan:
            if self._cancel:
                break
            try:
                src.rename(dst)
                ok += 1
                self.line.emit(f"  [완료] {src.name}\n      → {dst.name}")
            except OSError as e:
                fail += 1
                self.line.emit(f"  [실패] {src.name}: {e}")
        self.line.emit(f"\n[요약] 완료 {ok} / 실패 {fail}")


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("rename — 방송 릴 파일명 정리")
        self.resize(920, 620)
        self.plan: list[tuple[Path, Path]] = []
        self.worker: Worker | None = None

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"\\192.168.0.2\영상2\예능\... 또는 D:\...")
        btn_browse = QPushButton("찾아보기…")
        btn_browse.clicked.connect(self._browse)

        self.strip_season = False
        self.btn_preview = QPushButton("미리보기")
        self.btn_preview.clicked.connect(self._start_preview)
        self.btn_season = QPushButton("시즌제거")
        self.btn_season.clicked.connect(self._toggle_season)
        self.btn_apply = QPushButton("적용")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._start_apply)
        self.btn_stop = QPushButton("중지")
        self.btn_stop.setEnabled(False)  # 작업 실행 중에만 활성
        self.btn_stop.clicked.connect(self._stop_worker)

        self.btn_smi = QPushButton("자막변환")
        self.btn_smi.clicked.connect(self._start_smi)
        self.chk_del = QCheckBox("자막변환후원본제거")

        self.btn_mux = QPushButton("합치기")
        self.btn_mux.clicked.connect(lambda: self._start_mux(test=False))
        self.btn_muxtest = QPushButton("합치기 테스트")
        self.btn_muxtest.clicked.connect(lambda: self._start_mux(test=True))
        self.rad_remux = QRadioButton("mkv변환")
        self.rad_flag = QRadioButton("기본해제+ko추가")
        self.rad_strip = QRadioButton("자막제거+ko추가")
        self.rad_delsub = QRadioButton("자막제거")
        self.rad_remux.setChecked(True)

        # 남길 자막(자막제거 계열 모드 전용): 선택 언어 자막은 제거하지 않는다
        self.lbl_keep = QLabel("남길 자막")
        self.chk_keep_ko = QCheckBox("ko")
        self.chk_keep_en = QCheckBox("en")
        self.chk_keep_und = QCheckBox("und")
        # 동시 작업 수: 합치기 계열 전체 실행에서 병렬로 처리할 파일 수
        self.lbl_jobs = QLabel("동시 작업")
        self.spin_jobs = QSpinBox()
        self.spin_jobs.setRange(1, 4)
        for rad in (self.rad_remux, self.rad_flag, self.rad_strip, self.rad_delsub):
            rad.toggled.connect(self._update_keep_enabled)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        top = QHBoxLayout()
        top.addWidget(QLabel("경로:"))
        top.addWidget(self.path_edit, 1)
        top.addWidget(btn_browse)
        top.addWidget(self.btn_preview)
        top.addWidget(self.btn_season)
        top.addWidget(self.btn_apply)
        top.addWidget(self.btn_stop)

        # 메뉴바: 체크 가능한 [최상위] — 창을 항상 위에 고정
        menubar = QMenuBar(self)
        self.act_topmost = QAction("최상위", self)
        self.act_topmost.setCheckable(True)
        self.act_topmost.toggled.connect(self._set_topmost)
        menubar.addAction(self.act_topmost)

        row2 = QHBoxLayout()
        row2.addWidget(self.btn_smi)
        row2.addWidget(self.chk_del)
        row2.addSpacing(24)
        row2.addWidget(self.rad_remux)
        row2.addWidget(self.rad_flag)
        row2.addWidget(self.rad_strip)
        row2.addWidget(self.rad_delsub)
        row2.addSpacing(12)
        row2.addWidget(self.btn_mux)
        row2.addWidget(self.btn_muxtest)
        row2.addStretch(1)

        row3 = QHBoxLayout()
        row3.addWidget(self.lbl_keep)
        row3.addWidget(self.chk_keep_ko)
        row3.addWidget(self.chk_keep_en)
        row3.addWidget(self.chk_keep_und)
        row3.addSpacing(24)
        row3.addWidget(self.lbl_jobs)
        row3.addWidget(self.spin_jobs)
        row3.addStretch(1)

        root_layout = QVBoxLayout(self)
        root_layout.setMenuBar(menubar)
        root_layout.addLayout(top)
        root_layout.addLayout(row2)
        root_layout.addLayout(row3)
        root_layout.addWidget(self.log, 1)

        # 경로가 바뀌면 이전 미리보기 결과는 무효 — 재스캔 강제
        self.path_edit.textChanged.connect(self._invalidate_plan)

        # 옵션 영속화: 저장값 복원(복원 중 시그널이 같은 값을 재저장해도 무해)
        self.settings = QSettings("mkvtoolnix", "rename_gui")
        # 창 위치/크기 복원은 show 전에 — 최상위 복원이 show 를 먼저 부르면
        # 기본 위치로 떴다가 이동하는 점프가 보인다
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.chk_del.toggled.connect(
            lambda on: self.settings.setValue("delete_smi", on))
        self.chk_del.setChecked(bool(self.settings.value("delete_smi", True, type=bool)))
        for rad, key in ((self.rad_remux, "remux"), (self.rad_flag, "flag"),
                         (self.rad_strip, "strip"), (self.rad_delsub, "delsub")):
            rad.toggled.connect(
                lambda on, k=key: on and self.settings.setValue("mux_mode", k))
        saved_mode = str(self.settings.value("mux_mode", "remux", type=str))
        {"flag": self.rad_flag, "strip": self.rad_strip,
         "delsub": self.rad_delsub}.get(saved_mode, self.rad_remux).setChecked(True)
        for chk, key in ((self.chk_keep_ko, "keep_ko"), (self.chk_keep_en, "keep_en"),
                         (self.chk_keep_und, "keep_und")):
            chk.toggled.connect(lambda on, k=key: self.settings.setValue(k, on))
            chk.setChecked(bool(self.settings.value(key, False, type=bool)))
        self.spin_jobs.valueChanged.connect(
            lambda v: self.settings.setValue("jobs", v))
        self.spin_jobs.setValue(int(self.settings.value("jobs", 1, type=int)))
        self._update_keep_enabled()
        if self.settings.value("strip_season", False, type=bool):
            self.strip_season = True
            self.btn_season.setText("시즌제거취소")
        if self.settings.value("topmost", False, type=bool):
            self.act_topmost.setChecked(True)  # toggled → _set_topmost 적용
        self.path_edit.setText(str(self.settings.value("last_path", "", type=str)))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택", self.path_edit.text())
        if folder:
            self.path_edit.setText(folder)

    def _invalidate_plan(self) -> None:
        self.plan = []
        self.btn_apply.setEnabled(False)

    def _set_busy(self, busy: bool) -> None:
        self.btn_preview.setEnabled(not busy)
        self.btn_season.setEnabled(not busy)
        self.btn_smi.setEnabled(not busy)
        self.chk_del.setEnabled(not busy)
        self.btn_mux.setEnabled(not busy)
        self.btn_muxtest.setEnabled(not busy)
        self.rad_remux.setEnabled(not busy)
        self.rad_flag.setEnabled(not busy)
        self.rad_strip.setEnabled(not busy)
        self.rad_delsub.setEnabled(not busy)
        self.spin_jobs.setEnabled(not busy)
        self.path_edit.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        if busy:
            self.btn_apply.setEnabled(False)
            for w in (self.lbl_keep, self.chk_keep_ko,
                      self.chk_keep_en, self.chk_keep_und):
                w.setEnabled(False)
        else:
            self._update_keep_enabled()

    def _stop_worker(self) -> None:
        """실행 중 작업 중단: 남은 파일 스킵 + 현재 mkvmerge 즉시 종료·정리."""
        if self.worker is not None and self.worker.isRunning():
            self.btn_stop.setEnabled(False)
            self.log.appendPlainText("[중단 요청] 실행 중 mkvmerge 를 종료하고 정리한다…")
            self.worker.stop()

    def closeEvent(self, event: QCloseEvent) -> None:
        """창을 닫으면 진행 중 작업을 중단하고 정리가 끝난 뒤 종료한다."""
        self.settings.setValue("geometry", self.saveGeometry())
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(10_000)  # 현재 파일 정리(임시 삭제)까지 대기
        event.accept()

    def _update_keep_enabled(self, *_: object) -> None:
        """남길 자막 체크박스는 자막제거 계열 모드에서만 활성화한다."""
        on = self.rad_strip.isChecked() or self.rad_delsub.isChecked()
        for w in (self.lbl_keep, self.chk_keep_ko,
                  self.chk_keep_en, self.chk_keep_und):
            w.setEnabled(on)

    def _set_topmost(self, on: bool) -> None:
        """항상 위 고정 토글. 플래그 변경 시 창이 숨겨지므로 재표시가 필요하다."""
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()
        self.settings.setValue("topmost", on)

    def _toggle_season(self) -> None:
        """시즌제거 모드 토글 — 라벨 갱신 후 현재 경로로 즉시 재미리보기."""
        self.strip_season = not self.strip_season
        self.btn_season.setText("시즌제거취소" if self.strip_season else "시즌제거")
        self.settings.setValue("strip_season", self.strip_season)
        self._start_preview()

    def _get_root(self) -> Path | None:
        """경로 입력을 검증한다. 빈 입력이 Path('.') 로 새는 것을 차단한다."""
        text = self.path_edit.text().strip().strip('"')
        if not text:
            self.log.setPlainText("[오류] 경로를 입력하라")
            return None
        root = Path(text)
        if not root.is_dir():
            self.log.setPlainText(f"[오류] 폴더가 아니거나 접근 불가: {root}")
            return None
        self.settings.setValue("last_path", text)
        return root

    def _start_preview(self) -> None:
        root = self._get_root()
        if root is None:
            return
        self.log.clear()
        self._set_busy(True)
        self.worker = Worker("preview", root=root, strip_season=self.strip_season)
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.done.connect(self._on_preview_done)
        self.worker.start()

    def _on_preview_done(self, plan: list[tuple[Path, Path]]) -> None:
        self.plan = plan
        self._set_busy(False)
        self.btn_apply.setEnabled(bool(plan))

    def _start_smi(self) -> None:
        root = self._get_root()
        if root is None:
            return
        self.log.clear()
        self._set_busy(True)
        self.worker = Worker("smi", root=root, delete_smi=self.chk_del.isChecked())
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.done.connect(lambda _: self._on_task_done("자막변환"))
        self.worker.start()

    def _on_task_done(self, label: str) -> None:
        cancelled = self.worker is not None and self.worker._cancel
        self._invalidate_plan()  # 파일 생성/삭제/교체로 기존 미리보기 계획은 무효
        self._set_busy(False)
        if not cancelled:
            self._notify_done(label)

    def _notify_done(self, label: str) -> None:
        """작업 완료 알림. 창이 전면이 아니면 전면으로 올린 뒤 메시지를 띄운다."""
        if not self.isVisible():
            return  # 창을 닫으며 중단된 경우 — 알림 생략
        forced = False
        if not self.isActiveWindow():
            # Windows 는 백그라운드 프로세스의 전면 전환을 제한하므로
            # 임시 최상위 플래그로 강제하고 알림 종료 후 원상 복구한다
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            if not self.act_topmost.isChecked():
                self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                forced = True
            self.show()
            self.raise_()
            self.activateWindow()
        QMessageBox.information(self, "작업 완료", f"{label} 작업이 끝났다.")
        if forced:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self.show()

    def _start_mux(self, test: bool = False) -> None:
        root = self._get_root()
        if root is None:
            return
        self.log.clear()
        self._set_busy(True)
        if self.rad_remux.isChecked():
            mode = "remux"
        elif self.rad_delsub.isChecked():
            mode = "delsub"
        else:
            mode = "mux"
        langs: list[str] = []
        if self.rad_strip.isChecked() or self.rad_delsub.isChecked():
            for chk, code in ((self.chk_keep_ko, "ko"), (self.chk_keep_en, "en"),
                              (self.chk_keep_und, "und")):
                if chk.isChecked():
                    langs.append(code)
        self.worker = Worker(mode, root=root,
                             mux_strip=self.rad_strip.isChecked(), mux_test=test,
                             keep_langs=tuple(langs), jobs=self.spin_jobs.value())
        self.worker.line.connect(self.log.appendPlainText)
        label = "합치기 테스트" if test else "합치기"
        self.worker.done.connect(lambda _, l=label: self._on_task_done(l))
        self.worker.start()

    def _start_apply(self) -> None:
        self.log.appendPlainText("\n[적용 시작]")
        self._set_busy(True)
        self.worker = Worker("apply", plan=self.plan)
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.done.connect(self._on_apply_done)
        self.worker.start()

    def _on_apply_done(self, _: object) -> None:
        self.plan = []  # 적용 완료 — 재적용 방지, 새 미리보기 필요
        self._set_busy(False)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
