@echo off
REM Starts the OpenTelemetry demo WITH Prometheus + Jaeger (observability). Double-click it, or run from anywhere.
cd /d "%~dp0.."
echo Starting demo from %CD% ...
docker compose -f compose.yaml -f compose.observability.yaml up -d
if errorlevel 1 (
  echo.
  echo FAILED - probably a slow download. Switch to a phone hotspot and run this file again. It resumes.
  pause
  exit /b 1
)
echo.
echo Demo started. Wait 3 minutes, then in oncall-agent run:  python -m oncall.doctor
pause
