"""Run test builds one after another in a detached process (started by bg3_test_build(background=True)), logging to a file:
a full build is ~10 minutes, longer than an MCP call should block.

  python -m bg3data.runbuilds LOGFILE LAYER [--to N] [--start N] BUILD [BUILD ...]
Each report line is written as it happens (a build's progress shows while it runs).
"""
import sys
import time


def main(argv):
    log_path, layer, rest = argv[0], argv[1], list(argv[2:])
    opts = {}
    while rest and rest[0] in ("--to", "--start"):
        opts[rest[0]] = int(rest[1])
        rest = rest[2:]
    builds = rest
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
                rep = testing.run_build(s, active, layer, b, to_level=opts.get("--to"), start_level=opts.get("--start"), progress=w)
                if rep.startswith(f"build {b}:"):
                    rep = None              # already written line by line
            except Exception as e:          # one build's crash shouldn't stop the batch
                rep = f"build {b}: ERROR {type(e).__name__}: {e}"
            if rep:
                w(rep)
            w(f"=== {b} finished in {time.time() - t:.0f}s")
        w(f"all done {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main(sys.argv[1:])
