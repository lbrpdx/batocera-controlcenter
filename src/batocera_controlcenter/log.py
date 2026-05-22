# Log system for the control center
import os
import sys
import faulthandler
import traceback
import datetime

LOGFILE = "/var/log/controlcenter.log"
# Activate for some verbose message on tricky parts of the code
DEBUG = os.environ.get('CONTROLCENTER_DEBUG', '').lower() in ('1', 'true', 'yes')

# Open one file for all crash info
crash_file = open("/var/log/controlcenter-crash.log", "a")

# Enable faulthandler for hard crashes
faulthandler.enable(file=crash_file, all_threads=True)

# Global hook for uncaught Python exceptions
def global_excepthook(exc_type, exc_value, exc_tb):
    crash_file.write("Uncaught Python exception:\n")
    traceback.print_exception(exc_type, exc_value, exc_tb, file=crash_file)
    crash_file.flush()

sys.excepthook = global_excepthook

def debug_print(msg: str):
    if DEBUG:
        ts = datetime.datetime.now().strftime("%m/%d %H:%M:%S")
        with open(LOGFILE, "a") as f:
            f.write(f"[{ts}] {msg} \n")
