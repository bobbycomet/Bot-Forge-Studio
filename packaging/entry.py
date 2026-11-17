"""PyInstaller entry point."""
import sys

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from botstudio.selftest import main
        sys.exit(main())
    from botstudio.ui.main_window import run
    sys.exit(run(sys.argv))
