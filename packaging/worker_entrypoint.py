import multiprocessing

from localsr.worker.cli import main

if __name__ == "__main__":
    # A frozen worker is its own sys.executable: any multiprocessing helper
    # process (for example the resource tracker) would otherwise rerun the
    # whole worker instead of the helper.
    multiprocessing.freeze_support()
    main()
