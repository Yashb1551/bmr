@echo off
cd /d "%~dp0"
echo Starting Batch Planner server...
echo Other devices on this network can browse to: http://%COMPUTERNAME%:8501
echo (or use this PC's IP address shown by ipconfig, on port 8501)
echo.
python -m streamlit run "Main Codes\Scheduler.py" --server.address 0.0.0.0 --server.port 8501
pause
