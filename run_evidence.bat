@echo off
rem 검증 증빙 모으기 - start.bat 로 서버를 켠 뒤 실행. 결과: docs\images, docs\evidence
chcp 65001 >nul
cd /d "%~dp0"
if not exist docs\evidence mkdir docs\evidence
call ..\..\venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo running> docs\evidence\run_log.txt
python capture_evidence.py > docs\evidence\run_log.txt 2>&1
echo DONE>> docs\evidence\run_log.txt
