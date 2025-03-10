from scipy.optimize import minimize
import numpy as np
from copy import deepcopy
from .emissions import fwd_p_g
from .emissions import bwd_p_o_given_x
from .emissions import bwd_p_one_o_given_g, bwd_p_all_o_given_g
from .emissions import fwd_p_x, fwd_p_x_cont, fwd_p_x_nocont
from .emissions import posterior_g, posterior_x, posterior_c
from .emissions import calc_ll, calc_full_ll
from ..utils.log import log_


def norm(self):
    """simple L2 - norm function to test parameter vector convergence and squarem steps"""
    return np.sqrt(np.nansum(np.power(self, 2)))


def update_ftau_numeric(old_F, old_tau, data, post_g, update_F=True):
    """updates the SFS parameters

    tau represents the conditional SFS entry, i.e. what proportion of sites are
    derived in any particular SFS category

    F is the inbreeding coefficient, i.e. what proportion of sites coalesce
    before the SFS entry

    Parameters
    ----------
    old_F : array[S x 1]
        old F
    old_tau : array[S x 1]
        old tau
    data : SlugData
        data object, used for various indices
    post_g : array[L x 3]
        Pr(G | O)
    update_F : bool, optional
        should F be updated

    Returns
    -------
    new_F : array[S x 1]
    new_tau : array[Sx 1]
    """

    if not update_F:
        raise NotImplemented("update_F=False currently disabled")

    tau, F = np.zeros(data.n_sfs), np.zeros(data.n_sfs)
    tau[:], F[:] = old_tau, old_F

    for k in range(data.n_sfs):
        G0, G1, G2 = np.sum(post_g[data.SNP2SFS == k], 0)

        def f(args):
            tau_, F_ = args

            c0 = F_ * (1 - tau_) + (1 - F_) * (1 - tau_) ** 2
            c1 = (1 - F_) * 2 * tau_ * (1 - tau_)
            c2 = F_ * tau_ + (1 - F_) * tau_ * tau_
            x = np.log(c0) * G0 + np.log(c1) * G1 + np.log(c2) * G2
            if np.isnan(x):
                raise ValueError("nan in likelihood")
            return -x

        init = (tau[k], F[k])
        bounds = (1e-10, 1 - 1e-10), (1e-10, 1 - 1e-10)

        prev = f(init)
        OO = minimize(f, init, bounds=bounds, method="L-BFGS-B")
        if not OO.success:
            log_.debug(f"error optimizing F={OO.x[1]}, tau={OO.x[0]}, for k={k}")
            log_.debug(f"G-ratio: {(G1/2 + G2) / (G0+G1+G2)} tau={OO.x[0]}")

        tau[k], F[k] = OO.x.tolist()

    return F, tau


def update_ftau(old_F, old_tau, data, post_g, update_F=True):
    """updates the SFS parameters

    tau represents the conditional SFS entry, i.e. what proportion of sites are
    derived in any particular SFS category

    F is the inbreeding coefficient, i.e. what proportion of sites coalesce
    before the SFS entry

    Parameters
    ----------
    old_F : array[S x 1]
        old F
    old_tau : array[S x 1]
        old tau
    data : SlugData
        data object, used for various indices
    post_g : array[L x 3]
        Pr(G | O)
    update_F : bool, optional
        should F be updated

    Returns
    -------
    new_F : array[S x 1]
    new_tau : array[Sx 1]
    """

    tau, F = np.zeros(data.n_sfs), np.zeros(data.n_sfs)
    tau[:], F[:] = old_tau, old_F

    for k in range(data.n_sfs):
        G0, G1, G2 = np.sum(post_g[data.SNP2SFS == k], 0)

        # update tau
        if G0 + G1 + G2 > 0:
            tau[k] = (G1 / 2.0 + G2) / (G0 + G1 + G2)
        else:
            tau[k] = 0.0

        # for F, exclude X-chromosome stuff
        if update_F:
            if data.haploid_snps is None:
                pass
            else:
                ix1 = data.SNP2SFS == k
                ix1[data.haploid_snps] = False
                if np.all(~ix1):
                    pass
                else:
                    G0, G1, G2 = np.sum(post_g[ix1], 0)

            F[k] = 2 * G0 / (2 * G0 + G1 + 1e-300) - G1 / (2 * G2 + G1 + 1e-300)
        np.clip(F, 0, 1, out=F)  # round to [0, 1]

    return F, tau


def update_c(post_c, READ2RG, n_rgs):
    """update c
    parameters:
    c - vector of contamination rates to updates
    post_c : Pr( C_i = k| O_i = j)
    REF, ALT: number of reference and alt alleles
    OBS2RG: indices for readgroup
    n_rgs: number of rgs
    """

    c = np.empty(n_rgs)

    for r in range(n_rgs):
        c[r] = np.mean(post_c[READ2RG == r])

    return c


def update_eb(post_x, R, F, two_errors=True):
    """
    post_x : Pr(X_i = j |.)
    i.e. each row of post_x gives the prob that X is and, der, respectively
    """
    R = R.astype("bool")
    R = np.array(R, "bool")
    # have error if i) not flipped , R==1, X=0 or ii) if flipped, R==1, X==1
    errors = np.sum(post_x[R & ~F, 0]) + np.sum(post_x[R & F, 1])
    # have no_error if i) not flipped , R==0, X=0 or ii) if flipped, R==0, X==1
    not_errors = np.sum(post_x[~R & ~F, 0]) + np.sum(post_x[~R & F, 1])

    # have bias if i) not flipped , R==0, X=1 or ii) if flipped, R==0, X==0
    bias = np.sum(post_x[~R & ~F, 1]) + np.sum(post_x[~R & F, 0])
    # have no_bias if i) not flipped , R==1, X=1 or ii) if flipped, R==1, X==0
    not_bias = np.sum(post_x[R & ~F, 1]) + np.sum(post_x[R & F, 0])

    if two_errors:
        e = errors / (errors + not_errors) if errors + not_errors > 0 else 0
        b = bias / (bias + not_bias) if bias + not_bias > 0 else 0
    else:
        e = (errors + bias) / (errors + bias + not_errors + not_bias)
        b = e

    return e, b


def update_pars(pars, data, controller):
    """update all parameters; 1 EM step"""
    O = controller

    pars = deepcopy(pars) if controller.copy_pars else pars

    """ calc unconditional forward probs Pr(G), Pr(C), Pr(A)
        - Pr(G | Z) are the probabilities of observing genotype G given what we know about the SFS,
            assuming its not contaminant.
                it's a L x 3 matrix, with entry (i,j) giving  P(G_i=j) for i=0,...L, j=0,1,2
        - Pr(A | psi) probability of a derived allele given its contaminant
                L x 1 vector, with entry (i) giving P(G_i=1 | contaminant)
        - Pr(C | psi) probability of a derived allele given its contaminant
                L x 1 vector, with entry (i) giving P(G_i=1 | contaminant)
    """
    fwd_g = fwd_p_g(data, pars)

    fwd_a = data.psi  # size [L x 1]
    fwd_c = pars.cont  # size [O x 1]

    """run backward algorithm to calculate Pr(O | .)"""
    bwd_x = bwd_p_o_given_x(data.READS, data.FLIPPED_READS, pars.e, pars.b)
    bwd_g1 = bwd_p_one_o_given_g(
        bwd_x, fwd_a, fwd_c, data.READ2SNP, data.READ2RG, data.n_reads
    )  # size [O x 3]
    bwd_g = bwd_p_all_o_given_g(bwd_g1, data.READ2SNP, data.n_snps)

    """remaining forward probs Pr(X| C=0, G), Pr(X | C=1, A), Pr(X | C, A G) """
    fwd_x_cont = fwd_p_x_cont(fwd_a, data.READ2SNP)  # size [L x 1]
    fwd_x_nocont = fwd_p_x_nocont(fwd_g, data.READ2SNP)  # size [L x 1]
    fwd_x = fwd_p_x(fwd_x_cont, fwd_x_nocont, fwd_c, data.READ2RG)

    if O.update_ftau:
        post_g = posterior_g(bwd_g, fwd_g)
        pars.prev_F[:], pars.prev_tau[:] = pars.F, pars.tau
        pars.F, pars.tau = update_ftau_numeric(
            pars.F, pars.tau, data, post_g, update_F=controller.update_F
        )

    if O.update_cont:
        post_c = posterior_c(bwd_x, fwd_x_nocont, fwd_x_cont, fwd_c, data.READ2RG)
        pars.prev_cont[:] = pars.cont
        pars.cont = update_c(post_c, data.READ2RG, data.n_rgs)

    if O.update_eb:
        post_x = posterior_x(bwd_x, fwd_x_cont, fwd_x_nocont, fwd_c, data.READ2RG)
        pars.prev_e, pars.prev_b = pars.e, pars.b
        pars.e, pars.b = update_eb(
            post_x, data.READS, data.FLIPPED_READS, two_errors=O.update_bias
        )

    if O.do_ll:  # should be avoided since it runs the entire E-step
        pars.ll, pars.prev_ll = calc_full_ll(data, pars), pars.ll

    return pars


def squarem(pars0, data, controller):
    """squarem port from R"""

    EPS = 1e-10
    controller.copy_pars = True  # required for squarem
    min_step = controller.squarem_min
    max_step = controller.squarem_max
    pars = pars0

    for i in range(controller.n_iter):
        pars1 = update_pars(pars, data, controller)
        Δp1 = pars1 - pars
        if (
            norm(Δp1) < controller.param_tol
            or pars1.ll - pars1.prev_ll < controller.ll_tol
        ):
            pars = pars1
            log_.info(f"stopping since parameters did not change in Δp1: {norm(Δp1)} ")
            break

        pars2 = update_pars(pars1, data, controller)
        Δp2 = pars2 - pars1
        if norm(Δp2) < controller.param_tol or pars2.ll - pars1.ll < controller.ll_tol:
            pars = pars2
            log_.info(
                f"stopping since parameters did not change in Δp2: {norm(Δp2)}, {pars2.ll -pars1.ll} "
            )
            break

        Δp3 = Δp2 - Δp1

        step_size = norm(Δp1) / norm(Δp3)
        step_size = np.clip(step_size, min_step, max_step)

        pars_sq = deepcopy(pars2)
        pars_sq.pars[:] = pars.pars + 2 * step_size * Δp1 + step_size * step_size * Δp3

        # "stabilization step if all parameters are in bounds
        if np.all(0 <= pars_sq.pars) and np.all(pars_sq.pars <= 1):
            pass
        else:
            pars_sq.pars[pars_sq.pars < 0] = EPS
            pars_sq.pars[pars_sq.pars > 1] = 1 - EPS

        pars_sq = update_pars(pars_sq, data, controller)

        log_.info(
            f"LLs p0 {pars.ll:.4f} | p1 {pars1.ll-pars.ll:.4f} | p2 {pars2.ll-pars1.ll:.4f} | psq {pars_sq.ll-pars2.ll:.4f}"
        )

        # ll did not improve
        if pars_sq.ll <= pars2.ll:
            pars = pars2
            if step_size >= max_step:
                max_step = np.maximum(
                    controller.squarem_max, max_step / controller.squarem_mstep
                )
        else:
            pars = pars_sq
            if step_size == max_step:
                max_step *= controller.squarem_mstep

        s = f"iter {i}: step: {step_size:.3f} | ll: {pars.ll:4f} "
        s += f"Δll : {pars.delta_ll:.6f} | e={pars.e[0]:.4f} | b={pars.b[0]:.4f}"
        s += f" | Δc : {pars.delta_cont:.4f} | Δtau : {pars.delta_tau:.4f} | ΔF : {pars.delta_F:.4f}"
        s += f" | Δ1 : {norm(Δp1):.4f}| Δ2:{norm(Δp2):.4f} "
        log_.info(s)

    return pars


def em(pars, data, controller):
    for i in range(controller.n_iter):
        update_pars(pars, data, controller)
        s = f"iter {i}: ll: {pars.ll:4f} | Δll : {pars.delta_ll:4f} | e={pars.e[0]:.4f} | b={pars.b[0]:.4f}"
        s += f" | Δc : {pars.delta_cont:.4f} | Δtau : {pars.delta_tau:.4f} | ΔF : {pars.delta_F:.4f}"
        log_.info(s)
        if pars.delta_ll < controller.ll_tol:
            break

    return pars
