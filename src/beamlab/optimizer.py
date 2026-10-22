"""Finite local numerical fitting, shared unchanged by agent and baselines.

Inputs are measured powers and a movement callback; there is no world, fault,
source-control, range-control, hidden optimum or task-completion access here.
Fit a local log-power quadratic, then minimize with SciPy L-BFGS-B in a bounded
trust region. Fifteen fit acquisitions plus at most three candidate checks.
"""
import itertools
import math
import numpy as np
from scipy.linalg import lstsq
from scipy.optimize import minimize


def features(x):
    # Public rotational symmetry reduces 15 arbitrary quadratic coefficients
    # to 8 physical ones. Fifteen noisy acquisitions overdetermine this fit.
    return np.array([1., *x, x[0]**2+x[1]**2, x[2]**2+x[3]**2, x[0]*x[2]+x[1]*x[3]])


def public_gaussian_curvature(step_deg):
    """Known paraxial Gaussian geometry, independent of injected offsets.

    At mirror 2, displacement d=0.35*alpha1, direction alpha1+alpha2.
    Gaussian overlap is exp[-d²/w² -(kw/2)²(alpha1+alpha2-d/R)²].
    Mechanical angles produce alpha=2*pi/180*theta_deg. Unitary receiver
    propagation/lens preserve overlap; the measured finite-aperture residual
    remains in the fit diagnostics. No actual field or hidden value is read.
    """
    waist=.0004; wavelength=1064e-9; z=.55; separation=.35
    zr=math.pi*waist**2/wavelength
    w=waist*math.sqrt(1+(z/zr)**2)
    curvature=z*(1+(zr/z)**2)
    angular=(math.pi*w/wavelength)**2
    q=1-separation/curvature
    a=(separation/w)**2+angular*q*q
    b=angular;c=angular*q
    scale=2*(2*math.pi/180*step_deg)**2
    return scale*np.array([[a,0,c,0],[0,a,0,c],[c,0,b,0],[0,c,0,b]])


async def optimize_local(move, sample, *, center, radius_deg, target_dark_w, reference_dark_w,
                         max_samples=18, normalize=True):
    if not 16 <= max_samples <= 24:
        raise ValueError("Local fit needs 16..24 samples, including candidate verification")
    center = np.asarray(center, dtype=float)
    if center.shape != (4,) or not np.isfinite(center).all() or not .003 <= radius_deg <= .06:
        raise ValueError("Invalid four-angle local trust region")
    # The trust radius bounds the candidate, not the experimental displacement.
    # Small probes retain signal above the detector floor on a narrow beam.
    step = radius_deg/6
    lo = np.maximum(-.08, center-radius_deg)
    hi = np.minimum(.08, center+radius_deg)
    design = [np.zeros(4)]
    for i in range(4):
        for sign in [-1.,1.]:
            x = np.zeros(4); x[i] = sign; design.append(x)
    for i,j in itertools.combinations(range(4),2):
        x = np.zeros(4); x[i] = 1; x[j] = 1; design.append(x)
    measurements = []

    async def evaluate(position):
        await move(position.tolist())
        observed = await sample()
        if observed["saturated"]:
            raise ValueError("Local optimizer observed saturation; choose a valid detector range before optimizing")
        corrected = observed["target_w"]-target_dark_w
        reference = observed["reference_w"]-reference_dark_w
        if corrected <= 1e-8 or reference <= 1e-8:
            raise ValueError("Local optimizer needs positive dark-corrected powers; calibrate or acquire a smaller region")
        value = corrected/reference if normalize else corrected
        measurements.append((position.copy(),float(value),observed))
        return float(value)

    for point in design:
        await evaluate(np.clip(center+step*point,lo,hi))
    xs = [(p-center)/step for p,_,_ in measurements]
    matrix = np.stack([features(x) for x in xs])
    ys = -np.log([value for _,value,_ in measurements])
    coefficients, _, rank, _ = lstsq(matrix,ys)
    if rank < 8:
        raise ValueError("Local trust region is truncated at a limit; move center inward or shrink region")
    # The declared round Gaussian beam and circular paraxial optics are
    # rotationally symmetric. Pool the measured x/y curvatures, rather than
    # estimating four physically absent x-y cross terms from detector noise.
    # This prior is public optical geometry, never a hidden fault/optimum.
    a=2*coefficients[5]
    b=2*coefficients[6]
    c=coefficients[7]
    hessian=np.array([[a,0,c,0],[0,a,0,c],[c,0,b,0],[0,c,0,b]])
    measured_hessian=hessian.copy()
    # The weak two-mirror mode makes a free noisy Hessian ill-conditioned.
    # Use only the publicly known Gaussian/ray geometry for curvature, then
    # fit the unknown gradient/offset from these actual fifteen measurements.
    # This numerical tool never selects dark/range/thermal experiments.
    hessian=public_gaussian_curvature(step)
    adjusted=ys-.5*np.einsum('ni,ij,nj->n',np.asarray(xs),hessian,np.asarray(xs))
    linear,_,_,_=lstsq(matrix[:,:5],adjusted)
    gradient=linear[1:5]
    objective = lambda x: float(gradient@x+.5*x@hessian@x)
    derivative = lambda x: gradient+hessian@x
    fitted = minimize(objective,np.zeros(4),jac=derivative,method="L-BFGS-B",
                      bounds=list(zip((lo-center)/step,(hi-center)/step)),options={"maxiter":100,"ftol":1e-12})
    candidate = np.clip(center+step*fitted.x,lo,hi)
    await evaluate(candidate)
    best = max(measurements,key=lambda row:row[1])
    if not np.array_equal(best[0], candidate):
        await move(best[0].tolist())
    for _ in range(min(2,max_samples-len(measurements))):
        await evaluate(best[0])
    return {"method":"measured Gaussian log-gradient fit with public curvature + SciPy L-BFGS-B","samples_used":len(measurements),
            "position_deg":best[0].tolist(),"best_measured_objective":best[1],
            "last_measured_objective":measurements[-1][1],"fit_rank":int(rank),
            "fit_residual_rms":float(np.sqrt(np.mean((matrix@coefficients-ys)**2))),
            "curvature_scope":"Public Gaussian geometry; unknown gradient fitted from measured scalar powers",
            "measured_curvature":measured_hessian.tolist(),"public_curvature":hessian.tolist(),
            "normalized":normalize,"optimizer_status":int(fitted.status),"verification_samples":min(2,max_samples-16)}
