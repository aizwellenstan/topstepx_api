REM Step 1: Create virtual environment
python -m venv trading_env

REM Step 2: Activate environment (Command Prompt)
trading_env\Scripts\activate

REM Step 2 (PowerShell alternative)
.\trading_env\Scripts\Activate.ps1

REM Step 3: Upgrade pip
pip install --upgrade pip

REM Step 4: Create requirements.txt with the following content
----------------------------------------
# Core web stack
fastapi==0.115.0
uvicorn[standard]==0.32.0
httpx==0.27.0
urllib3==2.2.3
requests==2.32.3
jinja2==3.1.4

# Performance extras
httptools==0.6.1
uvloop==0.19.0

# Utilities
pyyaml==6.0.2
schedule==1.2.1
----------------------------------------

REM Step 5: Install dependencies
pip install -r requirements.txt
