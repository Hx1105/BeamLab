"""Simulated device controllers using the unmodified official Python SDK."""
import asyncio
import json
from pathlib import Path
import time
from agnes_mhs import Device, CallError


class Mirror:
    limit_deg = .08

    def __init__(self, device_id, move_duration_s=.30, trace_dir=None):
        self.position = [0., 0.]
        self.moving = False
        self.move_duration_s = move_duration_s
        self._epoch = 0
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.before_move = lambda: None
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.device = Device(
            device_id, "tip_tilt", name=device_id, model="Simulated two-axis mirror",
            firmware="beamlab-0.1.0", resources={"axes": "reject"},
            profile={"notes": "Simulated mirror; mechanical degrees. Reflection doubles the angle. Limits ±0.08 deg; local stop, finite deadline; no automatic resume."},
            state={"x_deg": {"type": "number", "unit": "deg", "min": -.08, "max": .08},
                   "y_deg": {"type": "number", "unit": "deg", "min": -.08, "max": .08},
                   "moving": {"type": "boolean"}},
        )
        self._update()
        self.telemetry = self.device.source(
            "axes", "values", "Measured mechanical mirror positions; simulated encoder", hz=10,
            fields={"x_deg": {"type": "number", "unit": "deg"},
                    "y_deg": {"type": "number", "unit": "deg"}, "moving": {"type": "boolean"}},
        )
        self.precise = self.device.source("encoder_precise", "text", "Exact mechanical encoder readings as JSON; avoids Hub numeric text rounding", hz=10)
        self.device.on_stop = self.stop_local
        self.device.after = lambda: self.snapshot()

        @self.device.tool(
            "Move both axes to mechanical angles in degrees; reflected beam angle is twice this. Deadline 5 s; stop leaves actual partial position.",
            name="move", timeout=5, motion=True, uses=["axes"],
            params={k: {"type": "number", "minimum": -.08, "maximum": .08} for k in ("x_deg", "y_deg")},
            required=["x_deg", "y_deg"],
        )
        async def move(call, x_deg, y_deg):
            async with asyncio.timeout(4.8):
                await self.move_to(x_deg, y_deg, call)
            return {"detail": "Mirror at measured position.", "data": self.snapshot()}

    def snapshot(self):
        return {"x_deg": self.position[0], "y_deg": self.position[1], "moving": self.moving}

    def precise_encoder(self):
        return json.dumps(self.snapshot())

    def _update(self):
        self.device.update(**self.snapshot())
        if self.trace_dir:
            state = {"t": time.time(), "device": self.device.id, **self.snapshot()}
            (self.trace_dir/f"{self.device.id}-actual.json").write_text(json.dumps(state))
            with (self.trace_dir/"physical-motion.jsonl").open("a") as f:
                f.write(json.dumps(state)+"\n")

    def stop_local(self):
        self._epoch += 1
        self.moving = False
        self._update()

    async def move_to(self, x_deg, y_deg, call=None):
        self.before_move()
        target = [max(-self.limit_deg, min(self.limit_deg, float(v))) for v in (x_deg, y_deg)]
        start = self.position.copy()
        epoch = self._epoch
        begun = time.monotonic()
        self.moving = True
        self._update()
        try:
            while self._epoch == epoch:
                if time.monotonic()-begun > 4.5:
                    raise CallError("timeout", "Local mirror deadline exceeded")
                if call:
                    await call.checkpoint()
                fraction = min(1., (time.monotonic()-begun)/self.move_duration_s)
                self.position[:] = [a+(b-a)*fraction for a, b in zip(start, target)]
                self._update()
                if fraction >= 1:
                    break
                await asyncio.sleep(.02)
        finally:
            self.moving = False
            self._update()

    async def stream(self):
        while True:
            if self.precise.wants():
                self.precise.send({"text": self.precise_encoder()}, t=time.time())
            if self.telemetry.wants():
                self.telemetry.send(self.snapshot(), t=time.time())
            await asyncio.sleep(.01)


async def one_mirror_main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--id", default="mirror-01")
    args = parser.parse_args()
    mirror = Mirror(args.id, move_duration_s=2., trace_dir=args.trace)
    stream = asyncio.create_task(mirror.stream())
    try:
        await mirror.device.run(args.hub)
    finally:
        mirror.stop_local()
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(one_mirror_main())
