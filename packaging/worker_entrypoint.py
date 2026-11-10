import multiprocessing

from localsr.worker.host_runtime import prefer_newer_host_runtime

# Before any C++ library loads (Linux only): see localsr/worker/host_runtime.py.
prefer_newer_host_runtime()

from localsr.worker.cli import main  # noqa: E402

if __name__ == "__main__":
    # A frozen worker is its own sys.executable: any multiprocessing helper
    # process (for example the resource tracker) would otherwise rerun the
    # whole worker instead of the helper.
    multiprocessing.freeze_support()
    main()
