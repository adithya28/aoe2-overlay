import asyncio
import sys

import qasync
from PyQt6.QtWidgets import QApplication

from PlayerStats import PlayerStatsFetcher
from UI import MainWindow, load_custom_font
from proxy import run


async def run_background_tasks():
    """Start the proxy and the stats fetcher – they run forever."""
    await asyncio.gather(
        run(),
        PlayerStatsFetcher().run()
    )


def main():
    # 1. Create Qt Application
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    load_custom_font(app)

    # Global transparency
    app.setStyleSheet("""
        QWidget {
            background: transparent;
        }
        QTabWidget::pane {
            background: transparent;
        }
        QScrollArea {
            background: transparent;
        }
        QScrollArea > QWidget > QWidget {
            background: transparent;
        }
    """)

    window = MainWindow()
    window.show()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    loop.create_task(run_background_tasks())

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
