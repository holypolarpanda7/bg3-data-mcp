"""Run test builds one after another in a detached process (started by bg3_test_build(background=True)), logging to a file:
a full build is ~10 minutes, longer than an MCP call should block.

  python -m bg3data.runbuilds LOGFILE LAYER BUILD [BUILD ...]
"""
import sys
import time


def main(argv):
    log_path, layer, builds = argv[0], argv[1], argv[2:]
    from . import server, testing
    s, active = server._testing_store(None)
    with open(log_path, "a", encoding="utf-8") as log:
        def w(msg):
            log.write(msg + "\n")
            log.flush()
        w(f"started {time.strftime('%Y-%m-%d %H:%M:%S')}: {', '.join(builds)}")
        for b in builds:
            t = time.time()
            try:
                rep = testing.run_build(s, active, layer, b)
            except Exception as e:          # one build's crash shouldn't stop the batch
                rep = f"build {b}: ERROR {type(e).__name__}: {e}"
            w(rep)
            w(f"=== {b} finished in {time.time() - t:.0f}s")
        w(f"all done {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main(sys.argv[1:])
