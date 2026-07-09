from ui.theme import setup_theme
from ui.windows import MainApp


def main():
    setup_theme()
    app = MainApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
