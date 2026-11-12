"""Start the DengueWatch India web app.

    python run.py

Then open http://localhost:8050 in your browser.

While the app runs, the Step 10 scheduler keeps the data fresh (weather and risk every
morning, new IDSP reports every Monday, retraining monthly) and catches up on start-up
if the laptop was off. Set ENABLE_SCHEDULER=0 in .env to switch it off.
"""

import logging
import os

from denguewatch import config
from denguewatch.app import create_app
from denguewatch.scheduler.runner import start_scheduler

app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    # In debug mode Flask starts the app twice (a watcher and the real server); schedule only in the real one.
    if not config.FLASK_DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()
    app.run(host="127.0.0.1", port=config.PORT, debug=config.FLASK_DEBUG)
