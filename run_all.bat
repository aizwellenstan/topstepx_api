@echo off
REM Change to project directory
cd /d C:\Users\marin\workspace\topstepx_api

REM Start main server in a new window
start cmd /k call run.bat

REM Start relay server in a new window
start cmd /k call run_relay.bat

REM Start relay server in a new window
start cmd /k call run_relay2.bat

REM Start relay server in a new window
start cmd /k call run_relay3.bat