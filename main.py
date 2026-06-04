import threading
import time
import webbrowser

import uvicorn


def _open_browser():
    time.sleep(1.4)
    webbrowser.open("http://localhost:8765")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run("app.api:app", host="127.0.0.1", port=8765, reload=False, log_level="warning")
