#ifndef NC_GMRES_FALLBACK_H
#define NC_GMRES_FALLBACK_H
// Experimental restarted GMRES recovery for NumCalc's right-preconditioned
// operator. This is a numerical solver, not a mesh-convergence guarantee.
#include <algorithm>
#include <cmath>
#include <complex>
#include <vector>

static std::complex<double> NC_GMRES_Z(const Complex& v)
{
    return std::complex<double>(v.re(), v.im());
}
static Complex NC_GMRES_C(const std::complex<double>& v)
{
    return Complex(v.real(), v.imag());
}
static double NC_GMRES_Norm(const Vector<Complex>& v)
{
    long double sum = 0.0;
    for (int i = 0; i < v.size(); ++i) sum += v[i].qnorm();
    return std::sqrt(static_cast<double>(sum));
}
static std::complex<double> NC_GMRES_Dot(const Vector<Complex>& a,
                                          const Vector<Complex>& b)
{
    std::complex<double> dot(0., 0.);
    for (int i = 0; i < a.size(); ++i)
        dot += std::conj(NC_GMRES_Z(a[i])) * NC_GMRES_Z(b[i]);
    return dot;
}
// Verify A * (right-preconditioned unknown) = original RHS, independently
// of the CGS or GMRES recurrence residual.
static double NC_GMRES_TrueRelativeResidual(const Vector<Complex>& rhs,
                                             Vector<Complex>& candidate)
{
    Vector<Complex> product(candidate.size());
    NC_MatrixVectorMultiplication(candidate, product);
    long double residual2 = 0.0, rhs2 = 0.0;
    for (int i = 0; i < candidate.size(); ++i) {
        const Complex d = rhs[i] - product[i];
        residual2 += d.qnorm();
        rhs2 += rhs[i].qnorm();
    }
    return std::sqrt(static_cast<double>(residual2)) /
        std::max(std::sqrt(static_cast<double>(rhs2)), 1.e-300);
}

static bool NC_GMRES_Fallback(ofstream& NCout,
                               const Vector<Complex>& rhs,
                               Vector<Complex>& solution)
{
    typedef std::complex<double> Z;
    const int n = rhs.size();
    const int restart = 30;
    const int max_steps = 360;
    const double tolerance = 1.e-8;
    const double rhs_norm = std::max(NC_GMRES_Norm(rhs), 1.e-300);
    solution = Complex(0., 0.);
    int steps = 0;
    while (steps < max_steps) {
        Vector<Complex> ax(n);
        NC_MatrixVectorMultiplication(solution, ax);
        Vector<Complex> residual(n);
        for (int i = 0; i < n; ++i) residual[i] = rhs[i] - ax[i];
        const double beta = NC_GMRES_Norm(residual);
        const double true_rel = beta / rhs_norm;
        if (!std::isfinite(true_rel)) return false;
        if (true_rel <= tolerance) {
            NCout << "GMRES fallback: iterations = " << steps
                  << ", true relative residual = " << true_rel << endl;
            return true;
        }
        std::vector<Vector<Complex> > basis;
        basis.reserve(restart + 1);
        for (int i = 0; i < n; ++i) residual[i] /= beta;
        basis.push_back(residual);
        std::vector<std::vector<Z> > H(
            restart + 1, std::vector<Z>(restart, Z(0., 0.)));
        std::vector<double> c(restart, 1.);
        std::vector<Z> s(restart, Z(0., 0.)), g(restart + 1, Z(0., 0.));
        g[0] = beta;
        int used = 0;
        for (int k = 0; k < restart && steps < max_steps; ++k, ++steps) {
            Vector<Complex> w(n);
            NC_MatrixVectorMultiplication(basis[k], w);
            // Twice modified Gram-Schmidt to improve orthogonality.
            for (int pass = 0; pass < 2; ++pass)
                for (int j = 0; j <= k; ++j) {
                    const Z dot = NC_GMRES_Dot(basis[j], w);
                    H[j][k] += dot;
                    for (int i = 0; i < n; ++i)
                        w[i] -= NC_GMRES_C(dot * NC_GMRES_Z(basis[j][i]));
                }
            const double next = NC_GMRES_Norm(w);
            H[k+1][k] = next;
            if (next > 1.e-14) {
                for (int i = 0; i < n; ++i) w[i] /= next;
                basis.push_back(w);
            }
            for (int j = 0; j < k; ++j) {
                const Z h0 = H[j][k], h1 = H[j+1][k];
                H[j][k] = c[j] * h0 + s[j] * h1;
                H[j+1][k] = -std::conj(s[j]) * h0 + c[j] * h1;
            }
            const Z a = H[k][k], b = H[k+1][k];
            const double an = std::abs(a), bn = std::abs(b);
            const double rho = std::hypot(an, bn);
            if (!std::isfinite(rho) || rho < 1.e-30) return false;
            c[k] = an / rho;
            const Z phase = an > 0. ? a / an : Z(1., 0.);
            s[k] = phase * std::conj(b) / rho;
            H[k][k] = c[k] * a + s[k] * b;
            H[k+1][k] = Z(0., 0.);
            const Z g0 = g[k], g1 = g[k+1];
            g[k] = c[k] * g0 + s[k] * g1;
            g[k+1] = -std::conj(s[k]) * g0 + c[k] * g1;
            used = k + 1;
            if (std::abs(g[k+1]) / rhs_norm < tolerance || next <= 1.e-14)
                break;
        }
        if (!used) return false;
        std::vector<Z> y(used, Z(0., 0.));
        for (int j = used - 1; j >= 0; --j) {
            Z value = g[j];
            for (int k = j + 1; k < used; ++k)
                value -= H[j][k] * y[k];
            if (!std::isfinite(std::abs(H[j][j])) ||
                std::abs(H[j][j]) < 1.e-30) return false;
            y[j] = value / H[j][j];
        }
        for (int j = 0; j < used; ++j)
            for (int i = 0; i < n; ++i)
                solution[i] += NC_GMRES_C(y[j] * NC_GMRES_Z(basis[j][i]));
        const double check = NC_GMRES_TrueRelativeResidual(rhs, solution);
        if (!std::isfinite(check)) return false;
        if (check <= tolerance) {
            NCout << "GMRES fallback: iterations = " << steps
                  << ", true relative residual = " << check << endl;
            return true;
        }
    }
    NCout << "GMRES fallback failed after " << steps
          << " iterations, true relative residual = "
          << NC_GMRES_TrueRelativeResidual(rhs, solution) << endl;
    return false;
}
#endif
