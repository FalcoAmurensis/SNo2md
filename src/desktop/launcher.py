"""PyInstaller entry: divert spawned workers before importing the GUI."""
if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    from sno2md.gui import main
    main()
