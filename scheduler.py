import schedule
import time
import subprocess

def run_task():
    # Example: run a batch file or another Python script
    bat_file = r"C:\Users\Administrator\workspace\topstepx_api\flattern.bat"
    subprocess.run(bat_file, shell=True, check=True)
    print("Task executed at 09:06.")

def run_relay():
    # Example: run a batch file or another Python script
    bat_file = r"C:\Users\Administrator\workspace\topstepx_api\run_relay.bat"
    subprocess.run(bat_file, shell=True, check=True)
    print("Task executed at 08:21.")

# Schedule the task daily at 08:21
schedule.every().day.at("09:06").do(run_task)
schedule.every().day.at("18:00").do(run_relay)

while True:
    schedule.run_pending()
    time.sleep(1)
