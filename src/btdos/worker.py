"""Isolated workload process. No capture privileges, scanning on demo, or shell."""
import argparse
import ctypes
import math
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

from . import cases
from .config import load_plan


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True)
    p.add_argument("--stop-file", required=True)
    p.add_argument("--parent-pid", required=True, type=int)
    p.add_argument("--inspect-only", action="store_true")
    opts = p.parse_args()
    if sys.platform != "linux":
        raise RuntimeError("live workloads require native Linux/BlueZ")
    plan = load_plan(opts.plan)
    if os.getppid() != opts.parent_pid:
        raise RuntimeError("supervisor is missing")
    # Workload lifetime is bounded independently of the supervisor's own timer.
    libc = ctypes.CDLL(None)
    if libc.prctl(1, signal.SIGINT, 0, 0, 0) != 0:
        raise RuntimeError("could not install parent-death signal")
    if os.getppid() != opts.parent_pid:
        raise RuntimeError("supervisor exited during setup")
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    signal.alarm(math.ceil(plan.worker_budget))
    args = SimpleNamespace(target=plan.target, local=plan.local_address,
                           adapter=plan.adapter, uuid16=plan.sdp_uuid16,
                           char=plan.characteristic, rate=plan.rate,
                           seconds=plan.duration, max_ops=plan.max_ops,
                           cycles=plan.cycles, hold=plan.hold, gap=plan.gap,
                           stop_file=opts.stop_file, parent_pid=opts.parent_pid)
    try:
        cases.stopped(args)
        cases.log("worker_start", case=plan.case, parent_pid=opts.parent_pid)
        if opts.inspect_only:
            import asyncio
            asyncio.run(cases.ble_info(args))
        elif plan.case == "br-sdp":
            cases.br_sdp(args)
        elif plan.case == "br-channel":
            cases.br_channel(args)
        else:
            import asyncio
            asyncio.run({"ble-read": cases.ble_read, "ble-cycle": cases.ble_cycle}[plan.case](args))
        cases.log("worker_end", status="completed")
        return 0
    except cases.IncompleteSDP as exc:
        cases.log("worker_end", status="needs_continuation", detail=str(exc))
        return 2
    except KeyboardInterrupt:
        cases.log("worker_end", status="interrupted")
        return 130
    except Exception as exc:
        cases.log("worker_end", status="error_stop", error=repr(exc))
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
