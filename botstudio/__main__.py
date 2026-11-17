import sys

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from .selftest import main
        sys.exit(main())
    from .ui.main_window import run
    sys.exit(run(sys.argv))
