"""Start the DengueWatch India web app.

    python run.py

Then open http://localhost:8050 in your browser.
"""

from denguewatch import config
from denguewatch.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=config.PORT, debug=config.FLASK_DEBUG)
