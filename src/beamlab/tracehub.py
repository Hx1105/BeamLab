"""Transparent loopback transport observer for raw MHS/MOS evidence.

No physical or model decision is implemented here. Messages are forwarded
unaltered; close codes propagate, preserving replacement and lost-link rules.
"""
import asyncio
import base64
import json
from pathlib import Path
import time
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


class Recorder:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def record(self, device, channel, direction, raw):
        envelope = {"observed_t": time.time(), "device": device, "channel": channel, "direction": direction}
        if isinstance(raw, bytes):
            envelope["binary_b64"] = base64.b64encode(raw).decode()
        else:
            envelope["raw"] = raw
            with (self.directory/f"{device}-messages.jsonl").open("a") as f:
                f.write(raw+"\n")
        with (self.directory/"wire.jsonl").open("a") as f:
            f.write(json.dumps(envelope)+"\n")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=44481)
    parser.add_argument("--upstream", default="ws://127.0.0.1:44480")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    recorder = Recorder(args.trace)
    control = Path(args.control)
    control.mkdir(parents=True, exist_ok=True)
    connections = {}

    async def handler(device_ws):
        path = device_ws.request.path
        if path not in ("/ws/mhs", "/ws/nerve"):
            await device_ws.close(1008, "Device channels only")
            return
        first = await device_ws.recv()
        parsed = json.loads(first)
        device = parsed["params"]["device"]["id"] if path == "/ws/mhs" else parsed["device"]
        channel = "command" if path == "/ws/mhs" else "nerve"
        recorder.record(device, channel, "device_to_hub", first)
        async with connect(args.upstream+path, subprotocols=["mhs.v1"], ping_interval=2, ping_timeout=3) as hub_ws:
            connections[(device, channel)] = (device_ws, hub_ws)
            await hub_ws.send(first)

            async def forward(source, target, direction):
                try:
                    async for raw in source:
                        recorder.record(device, channel, direction, raw)
                        await target.send(raw)
                except ConnectionClosed as exc:
                    code = exc.rcvd.code if exc.rcvd else 1011
                    await target.close(code if code != 1006 else 1011)
                finally:
                    await target.close()

            jobs = [asyncio.create_task(forward(device_ws, hub_ws, "device_to_hub")),
                    asyncio.create_task(forward(hub_ws, device_ws, "hub_to_device"))]
            try:
                await asyncio.gather(*jobs)
            finally:
                connections.pop((device, channel), None)
                for task in jobs:
                    task.cancel()

    async def operator_drop():
        path = control/"drop-once.json"
        while True:
            if path.exists():
                requested = json.loads(path.read_text())
                path.unlink()
                found = connections.get((requested["device"], "command"))
                if found:
                    for ws in found:
                        await ws.close(1011, "Independent lost-command-link test")
            await asyncio.sleep(.05)

    async with serve(handler, "127.0.0.1", args.port, subprotocols=["mhs.v1"], ping_interval=2, ping_timeout=3):
        print(json.dumps({"trace_proxy_ready": True, "port": args.port}), flush=True)
        await operator_drop()


if __name__ == "__main__":
    asyncio.run(main())
