import numpy as np


def voigt_to_tensor(C):
    """
    Convert a 6x6 stiffness matrix in standard Voigt notation

        [11, 22, 33, 23, 13, 12]

    to the full fourth-order stiffness tensor C_ijkl.

    Assumes the conventional engineering-strain Voigt notation:
        strain = [e11, e22, e33, 2*e23, 2*e13, 2*e12]

    Parameters
    ----------
    C : (6, 6) array_like
        Elastic stiffness matrix.

    Returns
    -------
    C4 : (3, 3, 3, 3) ndarray
        Fourth-order stiffness tensor.
    """
    C = np.asarray(C, dtype=float)

    if C.shape != (6, 6):
        raise ValueError("C must be a 6x6 stiffness matrix.")

    voigt_pairs = [
        (0, 0),  # 11
        (1, 1),  # 22
        (2, 2),  # 33
        (1, 2),  # 23
        (0, 2),  # 13
        (0, 1),  # 12
    ]

    C4 = np.zeros((3, 3, 3, 3))

    for I, (i, j) in enumerate(voigt_pairs):
        for J, (k, l) in enumerate(voigt_pairs):
            value = C[I, J]

            # Minor symmetries:
            # C_ijkl = C_jikl = C_ijlk = C_jilk
            C4[i, j, k, l] = value
            C4[j, i, k, l] = value
            C4[i, j, l, k] = value
            C4[j, i, l, k] = value

    return C4


def acoustic_velocities(C, rho, direction, units="GPa"):
    """
    Calculate acoustic phase velocities from a stiffness matrix.

    Parameters
    ----------
    C : (6, 6) array_like
        Stiffness matrix in standard Voigt notation.

    rho : float
        Mass density in kg/m^3.

    direction : array_like, shape (3,)
        Propagation direction, e.g.
            [1, 0, 0] -> [100]
            [1, 1, 0] -> [110]
            [1, 1, 1] -> [111]

        The vector does not need to be normalized.

    units : str
        Units of C:
            "Pa"
            "MPa"
            "GPa"

    Returns
    -------
    result : dict
        Contains:
            direction
            christoffel
            velocities
            polarizations
            longitudinal_index
            transverse_indices
            v_L
            v_T
    """

    C = np.asarray(C, dtype=float)
    n = np.asarray(direction, dtype=float)

    # ----------------------------------------------------------
    # Unit conversion
    # ----------------------------------------------------------
    unit_factors = {
        "Pa": 1.0,
        "MPa": 1e6,
        "GPa": 1e9,
    }

    if units not in unit_factors:
        raise ValueError("units must be 'Pa', 'MPa', or 'GPa'.")

    C = C * unit_factors[units]

    # ----------------------------------------------------------
    # Normalize propagation direction
    # ----------------------------------------------------------
    norm = np.linalg.norm(n)

    if norm == 0:
        raise ValueError("Propagation direction cannot be zero.")

    n = n / norm

    # ----------------------------------------------------------
    # Convert stiffness matrix to C_ijkl
    # ----------------------------------------------------------
    C4 = voigt_to_tensor(C)

    # ----------------------------------------------------------
    # Christoffel matrix
    #
    # Gamma_ik = C_ijkl n_j n_l
    # ----------------------------------------------------------
    Gamma = np.einsum("ijkl,j,l->ik", C4, n, n)

    # Numerical symmetrization
    Gamma = 0.5 * (Gamma + Gamma.T)

    # ----------------------------------------------------------
    # Solve Christoffel eigenvalue problem
    #
    # Gamma u = rho * v^2 * u
    # ----------------------------------------------------------
    eigenvalues, eigenvectors = np.linalg.eigh(Gamma)

    if np.any(eigenvalues < -1e-6):
        raise ValueError(
            "Negative Christoffel eigenvalue encountered. "
            "The stiffness tensor may be mechanically unstable."
        )

    # Remove tiny negative values caused by roundoff
    eigenvalues = np.maximum(eigenvalues, 0.0)

    velocities = np.sqrt(eigenvalues / rho)

    # eigenvectors[:, i] is polarization of mode i
    polarizations = eigenvectors

    # ----------------------------------------------------------
    # Determine which mode is most longitudinal.
    #
    # Longitudinality = |u . n|^2
    # ----------------------------------------------------------
    longitudinal_fraction = np.array([
        abs(np.dot(polarizations[:, i], n))**2
        for i in range(3)
    ])

    L_index = np.argmax(longitudinal_fraction)

    T_indices = [i for i in range(3) if i != L_index]

    v_L = velocities[L_index]
    v_T = velocities[T_indices]

    return {
        "direction": n,
        "christoffel": Gamma,
        "eigenvalues": eigenvalues,
        "velocities": velocities,
        "polarizations": polarizations,
        "longitudinal_fraction": longitudinal_fraction,
        "longitudinal_index": L_index,
        "transverse_indices": T_indices,
        "v_L": v_L,
        "v_T": v_T,
    }


# ==============================================================
# Example: Silicon
# ==============================================================

C11 = 165.6   # GPa
C12 = 63.9    # GPa
C44 = 79.5    # GPa

C_Si = np.array([
    [C11, C12, C12,   0,   0,   0],
    [C12, C11, C12,   0,   0,   0],
    [C12, C12, C11,   0,   0,   0],
    [  0,   0,   0, C44,   0,   0],
    [  0,   0,   0,   0, C44,   0],
    [  0,   0,   0,   0,   0, C44],
])

rho_Si = 2329.0  # kg/m^3


# Choose any propagation direction
direction = [1, 1, 0]

result = acoustic_velocities(
    C=C_Si,
    rho=rho_Si,
    direction=direction,
    units="GPa"
)


print("Propagation direction:")
print(result["direction"])

print("\nChristoffel matrix [GPa]:")
print(result["christoffel"] / 1e9)

print("\nAll acoustic velocities [m/s]:")
print(result["velocities"])

print("\nPolarization vectors:")
print(result["polarizations"])

print("\nLongitudinal fractions:")
print(result["longitudinal_fraction"])

print(f"\nQuasi-longitudinal velocity: {result['v_L']:.2f} m/s")

print("Quasi-transverse velocities:")
for v in result["v_T"]:
    print(f"    {v:.2f} m/s")



def fibonacci_sphere(n_points=10000):
    """
    Generate approximately uniformly distributed directions
    over the unit sphere.

    Returns
    -------
    directions : (n_points, 3) ndarray
        Unit vectors [nx, ny, nz].
    """
    i = np.arange(n_points)

    golden_angle = np.pi * (3.0 - np.sqrt(5.0))

    z = 1.0 - 2.0 * (i + 0.5) / n_points
    r = np.sqrt(1.0 - z**2)

    phi = golden_angle * i

    x = r * np.cos(phi)
    y = r * np.sin(phi)

    return np.column_stack((x, y, z))


# ============================================================
# Scan sphere
# ============================================================

n_points = 10000
directions = fibonacci_sphere(n_points)

vL = np.empty(n_points)
vT_slow = np.empty(n_points)
vT_fast = np.empty(n_points)

for i, direction in enumerate(directions):

    result = acoustic_velocities(
        C=C_Si,
        rho=rho_Si,
        direction=direction,
        units="GPa"
    )

    vL[i] = result["v_L"]

    # Sort the two transverse modes by speed
    vt = np.sort(result["v_T"])

    vT_slow[i] = vt[0]
    vT_fast[i] = vt[1]


# ============================================================
# Spherical averages
# ============================================================

vL_avg = np.mean(vL)
vT_slow_avg = np.mean(vT_slow)
vT_fast_avg = np.mean(vT_fast)

# Average over both transverse polarizations
vT_avg = np.mean(
    np.concatenate([vT_slow, vT_fast])
)


print("Spherical averages")
print("------------------")

print(f"<vL>       = {vL_avg:.2f} m/s")
print(f"<vT slow>  = {vT_slow_avg:.2f} m/s")
print(f"<vT fast>  = {vT_fast_avg:.2f} m/s")
print(f"<vT>       = {vT_avg:.2f} m/s")


# ============================================================
# Also find extrema
# ============================================================

iL_min = np.argmin(vL)
iL_max = np.argmax(vL)

iT_min = np.argmin(vT_slow)
iT_max = np.argmax(vT_fast)


print("\nExtrema")
print("-------")

print(
    f"min vL = {vL[iL_min]:.2f} m/s "
    f"at n = {directions[iL_min]}"
)

print(
    f"max vL = {vL[iL_max]:.2f} m/s "
    f"at n = {directions[iL_max]}"
)

print(
    f"min vT = {vT_slow[iT_min]:.2f} m/s "
    f"at n = {directions[iT_min]}"
)

print(
    f"max vT = {vT_fast[iT_max]:.2f} m/s "
    f"at n = {directions[iT_max]}"
)
