"""Pure-host iSTFT / streaming overlap-add oracle for tt-metal #54104.

The oracle deliberately mirrors the bounty's proposed matmul synthesis shape:
concatenate one-sided real/imaginary spectral coefficients, multiply by a static
inverse-DFT basis to recover a windowed time frame, multiply by the synthesis
window, overlap-add, and divide by the accumulated window-square denominator.

No TTNN or device behavior is claimed here.  This is an executable numerical
contract for a future on-device implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Optional, Sequence


class ISTFTContractError(ValueError):
    """Raised when the host iSTFT contract cannot reconstruct safely."""


def hann_window(length: int) -> list[float]:
    if length <= 0:
        raise ISTFTContractError("window length must be positive")
    if length == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * n / (length - 1)) for n in range(length)]


def make_one_sided_inverse_basis(n_fft: int, win_length: Optional[int] = None) -> list[list[float]]:
    """Return [win_length][2*n_freq] real-valued one-sided iDFT weights.

    Coefficients are ordered ``real[0:n_freq] + imag[0:n_freq]``.  For a real
    signal this exactly reconstructs the first ``win_length`` samples of the
    length-``n_fft`` inverse DFT when supplied a Hermitian one-sided spectrum.
    """
    if n_fft <= 0:
        raise ISTFTContractError("n_fft must be positive")
    if win_length is None:
        win_length = n_fft
    if win_length <= 0 or win_length > n_fft:
        raise ISTFTContractError("win_length must satisfy 0 < win_length <= n_fft")
    n_freq = n_fft // 2 + 1
    has_nyquist = n_fft % 2 == 0
    basis: list[list[float]] = []
    for n in range(win_length):
        row = [0.0] * (2 * n_freq)
        for k in range(n_freq):
            angle = 2.0 * math.pi * k * n / n_fft
            edge = k == 0 or (has_nyquist and k == n_fft // 2)
            scale = 1.0 / n_fft if edge else 2.0 / n_fft
            row[k] = scale * math.cos(angle)
            row[n_freq + k] = -scale * math.sin(angle)
        basis.append(row)
    return basis


def _validate_basis(basis: Sequence[Sequence[float]], n_coeff: int) -> int:
    if not basis:
        raise ISTFTContractError("inverse basis must not be empty")
    width = len(basis[0])
    if width != n_coeff:
        raise ISTFTContractError(f"inverse basis width {width} != coefficient width {n_coeff}")
    for row in basis:
        if len(row) != width:
            raise ISTFTContractError("inverse basis rows must have equal width")
    return len(basis)


def synthesize_frame(
    real: Sequence[float],
    imag: Sequence[float],
    inverse_basis: Sequence[Sequence[float]],
) -> list[float]:
    if len(real) != len(imag) or not real:
        raise ISTFTContractError("real and imag must have equal non-zero length")
    coeff = [float(x) for x in real] + [float(x) for x in imag]
    win_length = _validate_basis(inverse_basis, len(coeff))
    out = [0.0] * win_length
    for n, row in enumerate(inverse_basis):
        total = 0.0
        for weight, value in zip(row, coeff):
            total += float(weight) * value
        out[n] = total
    return out


def _normalized_slice(
    numerator: Sequence[float],
    denominator: Sequence[float],
    start: int,
    end: int,
    *,
    denominator_epsilon: float,
) -> list[float]:
    out: list[float] = []
    for idx in range(start, end):
        denom = denominator[idx]
        if denom <= denominator_epsilon:
            raise ISTFTContractError(
                f"overlap-add denominator is zero/unsafe at padded sample {idx}: {denom}"
            )
        out.append(numerator[idx] / denom)
    return out


def istft_full(
    frames: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    inverse_basis: Sequence[Sequence[float]],
    window: Sequence[float],
    hop_length: int,
    left_pad: int = 0,
    length: Optional[int] = None,
    denominator_epsilon: float = 1e-12,
) -> list[float]:
    if hop_length <= 0:
        raise ISTFTContractError("hop_length must be positive")
    if left_pad < 0:
        raise ISTFTContractError("left_pad must be non-negative")
    win_length = len(window)
    if win_length == 0:
        raise ISTFTContractError("window must not be empty")
    if hop_length > win_length:
        raise ISTFTContractError("hop_length > win_length creates uncovered samples")
    if not frames:
        return []

    total = (len(frames) - 1) * hop_length + win_length
    numerator = [0.0] * total
    denominator = [0.0] * total
    for frame_index, (real, imag) in enumerate(frames):
        time_frame = synthesize_frame(real, imag, inverse_basis)
        if len(time_frame) != win_length:
            raise ISTFTContractError("inverse basis/window length mismatch")
        start = frame_index * hop_length
        for n, value in enumerate(time_frame):
            w = float(window[n])
            numerator[start + n] += value * w
            denominator[start + n] += w * w

    out_start = left_pad
    out_end = total if length is None else min(total, left_pad + length)
    if out_start > out_end:
        raise ISTFTContractError("left_pad exceeds reconstructed extent")
    return _normalized_slice(
        numerator,
        denominator,
        out_start,
        out_end,
        denominator_epsilon=denominator_epsilon,
    )


@dataclass
class StreamingISTFT:
    inverse_basis: Sequence[Sequence[float]]
    window: Sequence[float]
    hop_length: int
    left_pad: int = 0
    length: Optional[int] = None
    denominator_epsilon: float = 1e-12

    def __post_init__(self) -> None:
        self.window = [float(x) for x in self.window]
        if not self.window:
            raise ISTFTContractError("window must not be empty")
        if self.hop_length <= 0 or self.hop_length > len(self.window):
            raise ISTFTContractError("hop_length must satisfy 0 < hop_length <= win_length")
        if self.left_pad < 0:
            raise ISTFTContractError("left_pad must be non-negative")
        if self.length is not None and self.length < 0:
            raise ISTFTContractError("length must be non-negative")
        self._numerator: list[float] = []
        self._denominator: list[float] = []
        self._frame_count = 0
        self._emitted_padded = 0
        self._output_count = 0
        self._flushed = False
        # Shape-check once using a dummy spectrum inferred from basis width.
        width = len(self.inverse_basis[0]) if self.inverse_basis else 0
        if width <= 0 or width % 2:
            raise ISTFTContractError("inverse basis width must be positive and even")
        self._n_freq = width // 2
        _validate_basis(self.inverse_basis, width)
        if len(self.inverse_basis) != len(self.window):
            raise ISTFTContractError("inverse basis row count must equal window length")

    def _ensure_extent(self, end: int) -> None:
        need = end - len(self._numerator)
        if need > 0:
            self._numerator.extend([0.0] * need)
            self._denominator.extend([0.0] * need)

    def _emit_until(self, padded_end: int) -> list[float]:
        padded_end = min(padded_end, len(self._numerator))
        start = max(self._emitted_padded, self.left_pad)
        if self.length is not None:
            padded_end = min(padded_end, self.left_pad + self.length)
        if padded_end <= start:
            self._emitted_padded = max(self._emitted_padded, padded_end)
            return []
        out = _normalized_slice(
            self._numerator,
            self._denominator,
            start,
            padded_end,
            denominator_epsilon=self.denominator_epsilon,
        )
        self._output_count += len(out)
        self._emitted_padded = padded_end
        return out

    def push(self, frames: Iterable[tuple[Sequence[float], Sequence[float]]]) -> list[float]:
        if self._flushed:
            raise ISTFTContractError("cannot push after flush")
        emitted: list[float] = []
        for real, imag in frames:
            if len(real) != self._n_freq or len(imag) != self._n_freq:
                raise ISTFTContractError(
                    f"stream frame n_freq mismatch: expected {self._n_freq}, got {len(real)}/{len(imag)}"
                )
            time_frame = synthesize_frame(real, imag, self.inverse_basis)
            start = self._frame_count * self.hop_length
            end = start + len(self.window)
            self._ensure_extent(end)
            for n, value in enumerate(time_frame):
                w = self.window[n]
                self._numerator[start + n] += value * w
                self._denominator[start + n] += w * w
            self._frame_count += 1
            # No future frame can touch samples before the next frame's start.
            emitted.extend(self._emit_until(self._frame_count * self.hop_length))
        return emitted

    def flush(self) -> list[float]:
        if self._flushed:
            return []
        self._flushed = True
        if self._frame_count == 0:
            return []
        return self._emit_until(len(self._numerator))


def dft_one_sided(frame: Sequence[float], n_fft: int) -> tuple[list[float], list[float]]:
    """Small stdlib reference DFT used only by the oracle tests/fixtures."""
    if len(frame) > n_fft:
        raise ISTFTContractError("frame longer than n_fft")
    padded = [float(x) for x in frame] + [0.0] * (n_fft - len(frame))
    n_freq = n_fft // 2 + 1
    real: list[float] = []
    imag: list[float] = []
    for k in range(n_freq):
        r = 0.0
        im = 0.0
        for n, value in enumerate(padded):
            angle = 2.0 * math.pi * k * n / n_fft
            r += value * math.cos(angle)
            im -= value * math.sin(angle)
        real.append(r)
        imag.append(im)
    return real, imag


def causal_stft_frames(
    signal: Sequence[float],
    *,
    n_fft: int,
    window: Sequence[float],
    hop_length: int,
    left_pad: int,
    flush_right_pad: bool = False,
) -> list[tuple[list[float], list[float]]]:
    if hop_length <= 0 or hop_length > len(window):
        raise ISTFTContractError("invalid hop_length")
    if left_pad < 0:
        raise ISTFTContractError("left_pad must be non-negative")
    padded = [0.0] * left_pad + [float(x) for x in signal]
    if flush_right_pad:
        # A causal Hann-like analysis can leave the final requested sample only
        # under the zero-valued window endpoint.  A terminal zero-padded frame
        # gives the tail one more non-zero overlap contribution, matching the
        # explicit streaming-flush boundary this oracle is designed to test.
        padded.extend([0.0] * max(0, len(window) - hop_length))
    frames: list[tuple[list[float], list[float]]] = []
    start = 0
    while start + len(window) <= len(padded):
        windowed = [padded[start + n] * float(window[n]) for n in range(len(window))]
        frames.append(dft_one_sided(windowed, n_fft))
        start += hop_length
    return frames
