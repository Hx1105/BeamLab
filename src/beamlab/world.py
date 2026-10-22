"""One shared CPU optical world. Hidden parameters are evaluator-owned.

Only public_snapshot and instrument acquisitions are agent observations.
Physical truth and complex fields are saved separately for independent judging.
Experiment time advances on acquisitions/controlled holds, equally for policies;
model/network latency is measured as wall time but does not alter drift/noise.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from agnes_mhs import Device, CallError
from .physics import OpticalBench
from .devices import Mirror


class World:
    target_fraction = .95
    reference_fraction = .05

    def __init__(self, noise=True, noise_seed=19001, trace_dir=None, n=128):
        self.bench = OpticalBench(n)
        self.noise = noise
        self.noise_seed = noise_seed
        self.trace_dir = Path(trace_dir) if trace_dir else None
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            (self.trace_dir/"evaluator").mkdir(exist_ok=True)
        self.mirrors = [Mirror(f"mirror-0{i+1}", move_duration_s=.08,
                               trace_dir=self.trace_dir/"actual" if self.trace_dir else None) for i in range(2)]
        self.hidden_offsets_deg = np.zeros(4)
        self.thermal_coeff_deg_per_k = np.zeros(4)
        self.source_multiplier = 1.
        self.source_power_w = .001
        self.target_gain = 1.
        self.reference_gain = 1.
        self.target_dark_w = 5e-6
        self.reference_dark_w = 1e-6
        self.range_w = .002
        self.noise_sigma_w = .7e-6
        self.shutter_open = True
        self.interlock_ok = True
        self.temperature_c = 22.
        self.temperature_ramp_k_per_s = 0.
        self.thermal_regulated = False
        self.sim_time_s = 0.
        self.target_measurements = 0
        self.reference_measurements = 0
        self.last_sample = None
        self.active_operation = None
        self.domain_epoch = 0
        self.acquisition_ids = set()
        self.mechanical_stuck = False
        self.last_field = None
        self.publish_sample = lambda sample: None
        self.sample_delay_s = .06
        self.thermal_setpoint_c = 22.

    def public_snapshot(self):
        return {"simulation":True,"angles_deg":[a for mirror in self.mirrors for a in mirror.position],
                "mirrors_moving":any(m.moving for m in self.mirrors),"shutter_open":self.shutter_open,
                "interlock_ok":self.interlock_ok,"source_setpoint_w":self.source_power_w,
                "range_w":self.range_w,"temperature_c":self.temperature_c,"thermal_regulated":self.thermal_regulated,
                "target_measurements":self.target_measurements,"budget_remaining":40-self.target_measurements,
                "sim_time_s":self.sim_time_s,"last_sample":self.last_sample,
                "reference_nominal_open_w":self.source_power_w*self.reference_fraction+1e-6,
                "nominal_good_ratio":self.target_fraction/self.reference_fraction,
                "nominal_target_dark_w":5e-6,"nominal_reference_dark_w":1e-6}

    def claim_acquisition(self, acquisition_id):
        if not isinstance(acquisition_id,str) or not 1<=len(acquisition_id)<=80:
            raise CallError('x_invalid_id','Acquisition needs an audit ID of 1..80 characters')
        if acquisition_id in self.acquisition_ids:
            raise CallError('x_replay','Duplicate acquisition ID refuses replay; read the prior actual state/result')
        self.acquisition_ids.add(acquisition_id)

    def save_public(self):
        if self.trace_dir:
            temp = self.trace_dir/"public-state.tmp"
            temp.write_text(json.dumps(self.public_snapshot()))
            temp.replace(self.trace_dir/"public-state.json")

    def set_shutter(self, opened):
        if opened and not self.interlock_ok:
            raise CallError("estop", "Interlock refuses an open beam")
        self.shutter_open = bool(opened)
        self.save_public()

    def advance(self, seconds):
        self.sim_time_s += seconds
        if self.thermal_regulated:
            self.temperature_c += (self.thermal_setpoint_c-self.temperature_c)*min(1., seconds/.6)
        else:
            self.temperature_c += self.temperature_ramp_k_per_s*seconds

    def optical_result(self):
        angles = np.array([a for m in self.mirrors for a in m.position])
        angles += self.hidden_offsets_deg + self.thermal_coeff_deg_per_k*(self.temperature_c-22)
        power = self.source_power_w*self.source_multiplier if self.shutter_open else 0.
        return self.bench.propagate(angles, source_power_w=power*self.target_fraction)

    def reference_sample(self, index=None):
        self.reference_measurements += 1
        power = self.source_power_w*self.source_multiplier*self.reference_fraction if self.shutter_open else 0.
        index = index if index is not None else self.reference_measurements+10000
        rng = np.random.default_rng(np.random.SeedSequence([self.noise_seed, index, 2]))
        return float(self.reference_gain*power+self.reference_dark_w+(rng.normal(0,.12e-6) if self.noise else 0.))

    def acquire(self, owner=None, acquisition_id=None):
        if any(m.moving for m in self.mirrors):
            raise CallError("x_interlock", "Cannot acquire while mirrors are moving")
        if self.active_operation is not None and owner != self.active_operation:
            raise CallError("x_interlock", "Domain operation owns shutter/mirrors/acquisition")
        if self.target_measurements >= 40:
            raise CallError("x_budget", "Target measurement budget exhausted; includes dark and optimizer samples")
        if self.shutter_open and not self.interlock_ok:
            raise CallError("estop", "Open beam without interlock")
        self.advance(.25)
        self.target_measurements += 1
        captured = time.time()
        field = self.optical_result()
        rng = np.random.default_rng(np.random.SeedSequence([self.noise_seed, self.target_measurements, 1]))
        analog = self.target_gain*field.received_power_w+self.target_dark_w+(rng.normal(0,self.noise_sigma_w) if self.noise else 0.)
        saturated = bool(analog >= self.range_w)
        sample = {"t":captured,"sample":self.target_measurements,"target_w":float(np.clip(analog,0,self.range_w)),
                  "reference_w":self.reference_sample(self.target_measurements),"saturated":saturated,
                  "range_w":self.range_w,"shutter_open":self.shutter_open,
                  "angles_deg":[a for m in self.mirrors for a in m.position],"temperature_c":self.temperature_c,
                  "sim_time_s":self.sim_time_s,"budget_remaining":40-self.target_measurements}
        if acquisition_id is not None:
            sample['acquisition_id']=acquisition_id
        self.last_sample = sample
        self.last_field = field
        self.save_public()
        if self.trace_dir:
            with (self.trace_dir/"measurements.jsonl").open("a") as f:
                f.write(json.dumps(sample)+"\n")
            truth = {**sample,"coupling_true":field.coupling,"source_multiplier":self.source_multiplier,
                     "effective_optical_angles_deg":(np.asarray(sample['angles_deg'])+self.hidden_offsets_deg+
                          self.thermal_coeff_deg_per_k*(self.temperature_c-22)).tolist(),
                     "source_integral_w":self.source_power_w*self.source_multiplier if self.shutter_open else 0.,
                     "transmitted_integral_w":field.transmitted_power_w,"received_mode_w":field.received_power_w,
                     "boundary_fraction":field.boundary_fraction,
                     "full_field_sha256":hashlib.sha256(field.field.tobytes()).hexdigest(),
                     "grid":self.bench.n if hasattr(self.bench,'n') else field.field.shape[0],
                     "target_gain":self.target_gain,"target_dark_w":self.target_dark_w,"reference_dark_w":self.reference_dark_w}
            # Audience geometry comes from this same acquired physical state;
            # it is not an MHS source or public agent observation.
            intensity=np.abs(field.field)**2
            total=float(intensity.sum())
            truth['receiver_centroid_mm']=[float(np.sum(intensity*self.bench.x)/max(total,1e-30)*1000),
                                            float(np.sum(intensity*self.bench.y)/max(total,1e-30)*1000)]
            with (self.trace_dir/"evaluator/physical-trace.jsonl").open("a") as f:
                f.write(json.dumps(truth)+"\n")
            np.savez_compressed(self.trace_dir/"evaluator/latest-field.npz",field=field.field,dx_m=self.bench.dx)
            fields=self.trace_dir/'evaluator/fields';fields.mkdir(exist_ok=True)
            stride=max(1,field.field.shape[0]//128)
            np.savez_compressed(fields/f'{self.target_measurements:03d}.npz',
                field=field.field[::stride,::stride].astype(np.complex64),dx_m=self.bench.dx*stride,
                original_grid=field.field.shape[0],display_sampling_stride=stride)
        self.publish_sample(sample)
        return sample

    def stop_mirrors(self):
        self.domain_epoch += 1
        for mirror in self.mirrors:
            mirror.stop_local()
        self.save_public()

    async def operator_perturb(self, command):
        """Independent audience/evaluator control, never declared as an MHS tool.

        One finite intervention and one genuine acquisition. A failed/busy or
        expired command is acknowledged as rejected and never automatically
        retried. It cannot reset budgets or resume a stopped numerical action.
        """
        if command.get('expires_unix',0)<time.time():
            raise CallError('x_expired','Independent command expired')
        if self.active_operation or any(m.moving for m in self.mirrors):
            raise CallError('x_busy','Independent intervention rejected while busy')
        if not self.shutter_open or not self.interlock_ok:
            raise CallError('x_interlock','Independent perturbation requires an open safe shutter')
        delta=np.asarray(command.get('offset_delta_deg'),dtype=float)
        if delta.shape!=(4,) or not np.isfinite(delta).all() or np.max(np.abs(delta))>.05:
            raise CallError('x_arguments','Operator perturbation outside finite bounds')
        identity='operator:'+command['id']
        self.claim_acquisition(identity)
        self.active_operation='operator'
        epoch=self.domain_epoch
        try:
            async with asyncio.timeout(1.5):
                await asyncio.sleep(self.sample_delay_s)
                if epoch!=self.domain_epoch:
                    raise CallError('x_local_stop','Independent intervention cancelled locally')
                self.hidden_offsets_deg += delta
                if self.trace_dir:
                    with (self.trace_dir/'evaluator/operator-injections.jsonl').open('a') as f:
                        f.write(json.dumps({'t':time.time(),**command,'applied':True})+'\n')
                sample=self.acquire(owner='operator',acquisition_id=identity)
                return {'applied':True,'sample':sample['sample'],'scope':'Independent intervention; actual counted acquisition'}
        finally:
            self.active_operation=None

    async def move_angles(self, angles, call=None):
        if self.mechanical_stuck:
            raise CallError("stuck", "Encoder made no progress; local controller refused continued motion. Maintenance needed.")
        for index, mirror in enumerate(self.mirrors):
            if call:
                await call.checkpoint()
            await mirror.move_to(*angles[2*index:2*index+2], call=call)
        self.save_public()

    def make_devices(self):
        source = Device("source-01", "light", name="Source and shutter", model="Simulated 1 mW source",
            profile={"notes":"Simulated low-power source. Nominal setpoint 0.001 W; reference tap 5%, bench 95%. Increasing power is not coupling recovery."},
            resources={"shutter":"reject"}, state={"open":{"type":"boolean"},"setpoint_w":{"type":"number","unit":"W"}})
        source.update(open=self.shutter_open,setpoint_w=self.source_power_w)
        def stop_source():
            self.set_shutter(False)
            source.update(open=False)
        source.on_stop = stop_source
        source.after = lambda: {"shutter_open":self.shutter_open,"source_setpoint_w":self.source_power_w}

        @source.tool("Open or close the source shutter for light/dark experiments. Interlock enforced locally.",
                     name="shutter",timeout=2,motion=True,uses=["shutter"],
                     params={"open":{"type":"boolean"}},required=["open"])
        async def shutter(call, open):
            if open and self.active_operation:
                raise CallError("x_interlock", "Acquisition or local optimization active; cannot open/change shutter")
            self.set_shutter(open)
            source.update(open=self.shutter_open)
            await asyncio.sleep(.01)
            return {"detail":"Shutter in measured position.","data":{"open":self.shutter_open}}

        target = Device("target-01", "power_meter", name="Target power meter",model="Simulated range-limited meter",
            profile={"notes":"Camera-free scalar receiver. Nominal good dark-corrected target/reference ratio 19. Nominal dark target 5e-6 W, reference 1e-6 W; changed bias must be measured. Every measure/dark/scan/fit/verification counts toward 40. Receiver field is unavailable to the agent."},
            resources={"acquisition":"reject"},
            state={"range_w":{"type":"number","unit":"W","min":.0002,"max":.002,"writable":True},
                   "measurements":{"type":"integer","min":0,"max":40},"remaining":{"type":"integer","min":0,"max":40}})
        target.update(range_w=self.range_w,measurements=self.target_measurements,remaining=40-self.target_measurements)
        samples = target.source("samples", "text", "Actual acquired scalar power samples as exact JSON; t is capture time. Event-driven; reading cached data creates no acquisition.")
        self.instrument_status = target.source('instrument_status','text',
            'Exact current range, budget, shutter and last acquisition time; live telemetry, not an extra power acquisition. Use to avoid numeric summary rounding.',hz=2)
        def publish(sample):
            target.update(measurements=self.target_measurements,remaining=40-self.target_measurements)
            sent=samples.send({"text":json.dumps(sample)},t=sample["t"])
            if self.trace_dir:
                with (self.trace_dir/'mos-delivery.jsonl').open('a') as f:
                    f.write(json.dumps({'sample':sample['sample'],'accepted_to_mos_outbox':sent,
                        'seq':samples.sent['seq'] if sent else None,
                        'scope':'Outbox admission; raw wire establishes actual delivery'})+'\n')
            return sent
        self.publish_sample = publish
        def set_range(name, value):
            if self.active_operation:
                raise CallError("x_interlock", "Cannot change detector range during an active domain operation")
            self.range_w = value
            target.update(range_w=value)
            # Official SDK update emits only changed fields. A range set that
            # clamps to its current value still deserves an actual snapshot.
            # This adapter emits a valid full state through the existing SDK
            # transport; the vendored SDK remains byte-for-byte unchanged.
            if target._cmd is not None:
                target._send_command({'jsonrpc':'2.0','method':'mhs/state',
                    'params':{'t':time.time(),'values':target.state}})
            self.save_public()
            return value
        target.on_set = set_range
        target.on_stop = self.stop_mirrors
        target.after = lambda: {"measurements":self.target_measurements,"remaining":40-self.target_measurements,"mirrors_moving":any(m.moving for m in self.mirrors)}
        target.unsafe = lambda tool: "Cross-device thermal operation or mirror moving" if self.active_operation=='condition' or (not self.active_operation and any(m.moving for m in self.mirrors)) else None

        @target.tool("Acquire one target and paired reference scalar sample. This spends one of 40 target measurements; dark acquisitions count too.",
                     name="measure",timeout=2,uses=["acquisition"],
                     params={"acquisition_id":{"type":"string","maxLength":80,"description":"Unique ID for this new acquisition, e.g. dark-1 or verify-3; reused IDs refuse replay"}},required=["acquisition_id"])
        async def measure(call,acquisition_id):
            self.claim_acquisition('target:'+acquisition_id)
            self.active_operation = "acquisition"
            try:
                async with asyncio.timeout(1.5):
                    await call.checkpoint()
                    await asyncio.sleep(self.sample_delay_s)
                    sample = self.acquire(owner="acquisition",acquisition_id=acquisition_id)
                return {"detail":"Real scalar acquisition completed.","data":sample}
            finally:
                self.active_operation = None

        @target.tool("Bounded local Gaussian log-gradient fit with public optical curvature and SciPy L-BFGS-B. Unknown gradient uses actual scalar powers, known curvature uses public waist 0.4 mm, wavelength 1064 nm, source-to-mirror2 0.55 m and mirror separation 0.35 m; no hidden offsets or field images. Costs 15 fit acquisitions + 1 candidate + up to 2 checks (16..18 total), so reserve 3 further final verification samples. radius_deg bounds the candidate; probes use radius/6. Needs positive unsaturated signal and an open shutter. Does not diagnose or choose shutter/range/thermal experiments. Shared unchanged with all baselines.",
            name="fit_local", timeout=30, uses=["acquisition"],motion=True,
            params={"radius_deg":{"type":"number","minimum":.003,"maximum":.06},
                    "target_dark_w":{"type":"number","minimum":0,"maximum":.0002},
                    "reference_dark_w":{"type":"number","minimum":0,"maximum":.00002},
                    "max_samples":{"type":"integer","minimum":16,"maximum":24},
                    "normalize":{"type":"boolean","default":True}},
            required=["radius_deg","target_dark_w","reference_dark_w","max_samples"])
        async def fit_local(call,radius_deg,target_dark_w,reference_dark_w,max_samples,normalize=True):
            from .optimizer import optimize_local
            if not self.shutter_open:
                raise CallError("x_interlock", "Open shutter before local optical fitting")
            if max_samples > 40-self.target_measurements:
                raise CallError("x_budget", "Requested fitting samples exceed remaining target budget")
            self.active_operation = "fit_local"
            epoch = self.domain_epoch
            def check_domain():
                if epoch != self.domain_epoch:
                    raise CallError('x_local_stop', 'Domain locally stopped; old optimizer cannot resume')
            async def move(angles):
                check_domain()
                await self.move_angles(angles,call)
            async def sample():
                check_domain()
                await call.checkpoint()
                await asyncio.sleep(self.sample_delay_s)
                measured = self.acquire(owner="fit_local")
                call.progress(done=measured["sample"],total=40,text="Measured local numerical fit, all samples counted")
                return measured
            try:
                async with asyncio.timeout(28):
                    result = await optimize_local(move,sample,center=[a for m in self.mirrors for a in m.position],
                        radius_deg=radius_deg,target_dark_w=target_dark_w,reference_dark_w=reference_dark_w,
                        max_samples=max_samples,normalize=normalize)
                return {"detail":"Bounded numerical fit completed; independently verify and diagnose from measurements.","data":result}
            except ValueError as exc:
                raise CallError("x_measurement", str(exc)) from exc
            finally:
                self.stop_mirrors()
                self.active_operation = None

        @target.tool("Scan one chosen mechanical axis over a bounded interval; acquire at each point and leave that axis at its best measured ratio. No fault diagnosis or whole-task recovery.",
            name="scan_axis",timeout=15,motion=True,uses=["acquisition"],
            params={"axis":{"type":"integer","minimum":0,"maximum":3},
                    "low_deg":{"type":"number","minimum":-.08,"maximum":.08},"high_deg":{"type":"number","minimum":-.08,"maximum":.08},
                    "points":{"type":"integer","minimum":3,"maximum":9},
                    "target_dark_w":{"type":"number","minimum":0,"maximum":.0002},
                    "reference_dark_w":{"type":"number","minimum":0,"maximum":.00002}},
            required=["axis","low_deg","high_deg","points","target_dark_w","reference_dark_w"])
        async def scan_axis(call,axis,low_deg,high_deg,points,target_dark_w,reference_dark_w):
            if not self.shutter_open or low_deg > high_deg:
                raise CallError("x_interlock", "Scan needs an open shutter and ordered interval")
            if points > 40-self.target_measurements:
                raise CallError("x_budget", "Scan exceeds remaining target measurements")
            self.active_operation = "scan_axis"
            epoch = self.domain_epoch
            center = [a for m in self.mirrors for a in m.position]
            rows = []
            try:
                async with asyncio.timeout(14):
                    for angle in np.linspace(low_deg,high_deg,points):
                        if epoch != self.domain_epoch:
                            raise CallError('x_local_stop','Domain scan locally stopped')
                        position = center.copy();position[axis]=float(angle)
                        await self.move_angles(position,call)
                        await asyncio.sleep(self.sample_delay_s)
                        observed = self.acquire(owner="scan_axis")
                        if observed["saturated"]:
                            raise CallError("x_measurement", "Scan found saturation; change range")
                        ratio = (observed["target_w"]-target_dark_w)/max(1e-9,observed["reference_w"]-reference_dark_w)
                        rows.append({"angle_deg":float(angle),"ratio":ratio})
                    best = max(rows,key=lambda x:x["ratio"])
                    center[axis]=best["angle_deg"]
                    await self.move_angles(center,call)
                return {"detail":"Chosen-axis scan completed.","data":{"axis":axis,"samples":rows,"position_deg":center}}
            finally:
                self.stop_mirrors()
                self.active_operation = None

        reference = Device("reference-01","photodiode",name="Reference photodiode",model="Simulated splitter tap",
            profile={"notes":"5% source tap, upstream of all mirrors. Gain 1; nominal dark 1e-6 W. A reference-only reading does not spend a target measurement."},resources={"sampling":"reject"})
        ref_samples = reference.source("samples","text","Actual reference-only photodiode acquisitions as exact JSON")
        @reference.tool("Acquire upstream splitter reference only to test source drift; no target sample is spent.",name="measure",timeout=2,uses=["sampling"],
            params={"acquisition_id":{"type":"string","maxLength":80}},required=["acquisition_id"])
        async def measure_reference(call,acquisition_id):
            self.claim_acquisition('reference:'+acquisition_id)
            await asyncio.sleep(self.sample_delay_s)
            captured = time.time()
            observed={"acquisition_id":acquisition_id,"reference_w":self.reference_sample(),"t":captured,"shutter_open":self.shutter_open,"nominal_open_w":self.source_power_w*.05+1e-6}
            ref_samples.send({"text":json.dumps(observed)},t=captured)
            return {"detail":"Upstream reference acquired.","data":observed}

        environment = Device("environment-01","environment",name="Environment and interlock",model="Simulated thermal enclosure",
            profile={"notes":"Air/mount temperature and local optical interlock. Experiment clock advances 0.25 s per target acquisition; a controlled thermal hold advances the same clock for all policies. Real network/model latency does not secretly change physical drift."},
            resources={"thermal":"reject"},state={"interlock_ok":{"type":"boolean"},"regulated":{"type":"boolean"}})
        environment.update(interlock_ok=self.interlock_ok,regulated=self.thermal_regulated)
        env_source=environment.source("telemetry","text","Measured temperature and interlock, exact JSON",hz=2)
        @environment.tool("Observe current temperature, regulator and interlock without a target acquisition.",name="observe",timeout=2,read_only=True)
        async def observe(call):
            return {"data":{"temperature_c":self.temperature_c,"sim_time_s":self.sim_time_s,"interlock_ok":self.interlock_ok,"regulated":self.thermal_regulated}}
        @environment.tool("Use the local thermal controller to settle the enclosure near a chosen setpoint in 20..24 C. Finite controlled hold; does not select optics or diagnose.",
            name="condition",timeout=5,motion=True,uses=["thermal"],params={"setpoint_c":{"type":"number","minimum":20,"maximum":24}},required=["setpoint_c"])
        async def condition(call,setpoint_c):
            if self.active_operation:
                raise CallError("x_interlock", "Cannot condition during an acquisition or optical optimization")
            self.thermal_setpoint_c=setpoint_c
            self.thermal_regulated=True
            self.active_operation="condition"
            try:
                async with asyncio.timeout(4):
                    for _ in range(8):
                        await call.checkpoint()
                        self.advance(.25)
                        self.save_public()
                        await asyncio.sleep(.05)
                return {"detail":"Thermal hold completed.","data":{"temperature_c":self.temperature_c,"regulated":True,"sim_time_s":self.sim_time_s}}
            finally:
                self.active_operation=None
                environment.update(regulated=self.thermal_regulated)
                self.save_public()
        def stop_thermal():
            self.thermal_regulated=False
            environment.update(regulated=False)
        environment.on_stop=stop_thermal
        for mirror in self.mirrors:
            mirror.device.unsafe=lambda tool: "Cross-device acquisition/optimization interlock active" if self.active_operation else None
            # Real mechanical error is a controller observation, not the hidden injection label.
            def checked_move():
                if self.mechanical_stuck:
                    raise CallError("stuck", "Encoder made no progress; maintenance required")
            mirror.before_move=checked_move
        self.devices=[source,*[m.device for m in self.mirrors],target,reference,environment]
        self.env_source=env_source
        self.save_public()
        return self.devices

    async def stream_environment(self):
        while True:
            if self.env_source.wants():
                self.env_source.send({"text":json.dumps({"temperature_c":self.temperature_c,"regulated":self.thermal_regulated,"interlock_ok":self.interlock_ok,"sim_time_s":self.sim_time_s})},t=time.time())
            await asyncio.sleep(.05)

    async def stream_instruments(self):
        while True:
            if self.instrument_status.wants():
                self.instrument_status.send({'text':json.dumps({'range_w':self.range_w,
                    'target_measurements':self.target_measurements,'budget_remaining':40-self.target_measurements,
                    'shutter_open':self.shutter_open,'active_operation':self.active_operation,
                    'last_capture_t':self.last_sample['t'] if self.last_sample else None,
                    'acquisition_status':'event-driven; no implicit power sampling'})},t=time.time())
            await asyncio.sleep(.05)
