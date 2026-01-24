@echo off
REM Change to project directory
cd /d C:\Users\marin\workspace\topstepx_api

REM Activate virtual environment
call trading_env\Scripts\activate.bat

REM Run Uvicorn server
uvicorn main:app --host 0.0.0.0 --port 5000 --http httptools
