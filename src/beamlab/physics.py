"""CPU complex-field optics. Distances are metres, field angles radians.

MHS mirror commands use mechanical degrees. A reflection doubles that angle.
LightPipes Tilt has a negative phase convention, so its argument is negated
to make positive bench angles produce positive transverse displacement.
Fields have sqrt(W)/m units after normalization; integral intensity is W.
"""
from dataclasses import dataclass
import copy
import math
import os

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
if hasattr(os, "sched_getaffinity"):
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])

import numpy as np
from LightPipes import Begin, GaussBeam, Tilt, Forvard, Lens, CircAperture, Power


@dataclass
class OpticalResult:
    field: np.ndarray
    coupling: float
    integral: float
    received_power_w: float
    transmitted_power_w: float
    boundary_fraction: float


class OpticalBench:
    wavelength_m = 1064e-9
    waist_m = .4e-3
    source_to_m1_m = .20
    m1_to_m2_m = .35
    m2_to_lens_m = .15
    lens_to_receiver_m = .20
    focal_m = .20
    aperture_radius_m = 2.0e-3

    def __init__(self, n=128, window_m=.010):
        self.n, self.window_m = n, window_m
        self.initial = GaussBeam(Begin(window_m, self.wavelength_m, n), self.waist_m)
        self.initial.field /= math.sqrt(float(Power(self.initial)))
        self.dx = float(self.initial.dx)
        self.y, self.x = self.initial.mgrid_cartesian
        self.mode = self._path(np.zeros(4), False)

    def gaussian_free(self, z_m):
        return Forvard(copy.deepcopy(self.initial), z_m)

    def reflection_probe(self, mechanical_deg, z_m):
        reflected_rad = 2 * math.radians(mechanical_deg)
        return Forvard(Tilt(copy.deepcopy(self.initial), -reflected_rad, 0), z_m)

    def moments(self, field):
        intensity = np.abs(field.field)**2
        total = float(intensity.sum())
        cx = float(np.sum(intensity*self.x)/total)
        cy = float(np.sum(intensity*self.y)/total)
        radius = math.sqrt(2 * float(np.sum(intensity*((self.x-cx)**2+(self.y-cy)**2))/total))
        return total*self.dx**2, cx, cy, radius

    def overlap(self, field, mode):
        integral = np.sum(np.conj(mode.field)*field.field)*self.dx**2
        return float(abs(integral)**2/(float(Power(mode))*float(Power(field))))

    def _path(self, angles_deg, aperture):
        reflected = 2 * np.deg2rad(np.asarray(angles_deg, dtype=float))
        f = Forvard(copy.deepcopy(self.initial), self.source_to_m1_m)
        f = Tilt(f, -reflected[0], -reflected[1])
        f = Forvard(f, self.m1_to_m2_m)
        f = Tilt(f, -reflected[2], -reflected[3])
        f = Forvard(f, self.m2_to_lens_m)
        if aperture:
            f = CircAperture(f, self.aperture_radius_m)
        f = Lens(f, self.focal_m)
        return Forvard(f, self.lens_to_receiver_m)

    def propagate(self, angles_deg, source_power_w=1.0, aperture=True):
        if source_power_w < 0 or not math.isfinite(source_power_w):
            raise ValueError("source power must be finite and nonnegative")
        if np.asarray(angles_deg).shape != (4,):
            raise ValueError("two dual-axis mirrors require four angles")
        field = self._path(angles_deg, aperture)
        integral = float(Power(field))
        # Absolute coupling includes aperture transmission. Never normalize it away.
        amplitude = np.sum(np.conj(self.mode.field)*field.field)*self.dx**2
        coupling = float(abs(amplitude)**2/float(Power(self.mode)))
        intensity = np.abs(field.field)**2
        mask = (np.abs(self.x) > .4*self.window_m) | (np.abs(self.y) > .4*self.window_m)
        edge = float(intensity[mask].sum()/intensity.sum())
        return OpticalResult(field.field*math.sqrt(source_power_w), coupling, integral,
                             source_power_w*coupling, source_power_w*integral, edge)
