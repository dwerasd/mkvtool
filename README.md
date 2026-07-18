# mkvtool

한국 방송릴 미디어 라이브러리 정리 도구 모음 (Python 3 + PySide6 GUI, mkvmerge 기반).

## 기능

- **파일명 규칙 통일** — 방송릴 파일명을 표준 형식으로 일괄 정규화. 미리보기 후 적용,
  시즌 접두 제거(S02E03 → E03) 토글, 찾기/바꾸기 단어변경 지원. (`rename.py`, GUI)
- **mkv 변환** — 비-MKV 영상(mp4/avi/ts/wmv/mov)을 트랙 전체 보존으로 MKV 컨테이너로
  변환. 검증 후 원본 제거.
- **자막변환** — SMI → SRT 일괄 변환(SubtitleEdit 엔진 우선, 내부 파서 폴백).
  변환 후 원본 .smi 제거 옵션. (`smi2srt.py`, GUI)
- **자막추가** — 같은 이름의 .srt 를 ko 기본 자막으로 MKV 에 합치기.
  기존 자막의 기본 플래그 해제 또는 전부 제거 모드 선택. 합쳐진 .srt 삭제 옵션.
- **자막제거** — MKV 자막 트랙 일괄 제거(용량 절감 보고). 남길 자막 언어(ko/en/und)
  선택 시 해당 자막은 보존하고 ko 를 기본 트랙으로 지정. (`del_sup.py`, GUI)
- **음성정리** — 자막제거 계열 모드에서 남길 음성 언어(ko/en/ja/und)를 선택해 그 외
  음성 트랙 제거, 기본 트랙으로 만들 언어 지정(ko/en/ja/und). 자막과 동시 처리.

## 안전 장치

- 모든 컨테이너 변경은 임시 파일 생성 → 트랙 구성 검증 → 원자 교체(실패 시 원본 보존).
- [합치기 테스트]로 첫 파일 1개만 선검증(원본 유지) 후 전체 실행.
- 실행 중 [중지]·창 닫기로 즉시 중단(임시 파일 정리, 원본 보존).

## 실행

```
python rename_gui.py            # GUI (전 기능 통합)
python rename.py "<폴더>" [--apply]
python smi2srt.py "<폴더>" [--overwrite]
python del_sup.py "<폴더>"
```

`mkvmerge.exe` 경로는 `.env` 의 `PATH_MKV_TOOLS`, SubtitleEdit 경로는 `PATH_SUBTITLE` 로 지정한다.
