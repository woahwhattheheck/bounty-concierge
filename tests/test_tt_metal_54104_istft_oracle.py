from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ORACLE_PATH = ROOT / "work" / "high-value" / "tt-metal-54104" / "istft_oracle.py"


def load_oracle():
    spec = importlib.util.spec_from_file_location("tt_metal_54104_istft_oracle", ORACLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_close(tc: unittest.TestCase, got, want, tol=1e-9):
    tc.assertEqual(len(got), len(want))
    for i, (g, w) in enumerate(zip(got, want)):
        tc.assertAlmostEqual(g, w, delta=tol, msg=f"index {i}")


class ISTFTOracleTests(unittest.TestCase):
    def setUp(self):
        self.o = load_oracle()

    def test_inverse_basis_round_trip_rectangular(self):
        signal = [0.25, -1.0, 2.0, 0.5, -0.75, 0.0, 1.5, -0.25]
        real, imag = self.o.dft_one_sided(signal, 8)
        basis = self.o.make_one_sided_inverse_basis(8)
        assert_close(self, self.o.synthesize_frame(real, imag, basis), signal, 2e-9)

    def test_odd_fft_inverse_basis_round_trip(self):
        signal = [0.2, -0.3, 0.5, 1.1, -0.7, 0.9, -0.4]
        real, imag = self.o.dft_one_sided(signal, 7)
        basis = self.o.make_one_sided_inverse_basis(7)
        assert_close(self, self.o.synthesize_frame(real, imag, basis), signal, 2e-9)

    def test_causal_hann_overlap_add_round_trip(self):
        win = self.o.hann_window(8)
        hop = 4
        left_pad = 4
        signal = [math.sin(0.21 * i) + 0.1 * math.cos(0.7 * i) for i in range(24)]
        frames = self.o.causal_stft_frames(
            signal,
            n_fft=8,
            window=win,
            hop_length=hop,
            left_pad=left_pad,
            flush_right_pad=True,
        )
        got = self.o.istft_full(
            frames,
            inverse_basis=self.o.make_one_sided_inverse_basis(8),
            window=win,
            hop_length=hop,
            left_pad=left_pad,
            length=len(signal),
        )
        assert_close(self, got, signal, 3e-9)

    def test_streaming_chunk_partitions_match_full(self):
        win = self.o.hann_window(8)
        hop = 4
        left_pad = 4
        signal = [math.sin(0.13 * i) - 0.2 * math.sin(0.51 * i) for i in range(32)]
        frames = self.o.causal_stft_frames(
            signal,
            n_fft=8,
            window=win,
            hop_length=hop,
            left_pad=left_pad,
            flush_right_pad=True,
        )
        basis = self.o.make_one_sided_inverse_basis(8)
        full = self.o.istft_full(
            frames,
            inverse_basis=basis,
            window=win,
            hop_length=hop,
            left_pad=left_pad,
            length=len(signal),
        )
        stream = self.o.StreamingISTFT(basis, win, hop, left_pad=left_pad, length=len(signal))
        out = []
        out.extend(stream.push(frames[:1]))
        out.extend(stream.push(frames[1:3]))
        out.extend(stream.push(frames[3:4]))
        out.extend(stream.push(frames[4:]))
        out.extend(stream.flush())
        assert_close(self, out, full, 1e-12)
        assert_close(self, out, signal, 3e-9)

    def test_streaming_single_frame_chunks_match_bulk(self):
        win = [1.0] * 6
        hop = 3
        signal = [float((i % 7) - 3) / 3 for i in range(18)]
        frames = self.o.causal_stft_frames(signal, n_fft=6, window=win, hop_length=hop, left_pad=3)
        basis = self.o.make_one_sided_inverse_basis(6)
        a = self.o.StreamingISTFT(basis, win, hop, left_pad=3, length=len(signal))
        b = self.o.StreamingISTFT(basis, win, hop, left_pad=3, length=len(signal))
        out_a = []
        for frame in frames:
            out_a.extend(a.push([frame]))
        out_a.extend(a.flush())
        out_b = b.push(frames) + b.flush()
        assert_close(self, out_a, out_b, 1e-12)
        assert_close(self, out_a, signal, 2e-9)

    def test_length_trim_is_exact(self):
        win = [1.0] * 4
        signal = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        frames = self.o.causal_stft_frames(signal, n_fft=4, window=win, hop_length=2, left_pad=2)
        got = self.o.istft_full(
            frames,
            inverse_basis=self.o.make_one_sided_inverse_basis(4),
            window=win,
            hop_length=2,
            left_pad=2,
            length=5,
        )
        assert_close(self, got, signal[:5], 1e-9)

    def test_zero_denominator_fails_closed(self):
        basis = self.o.make_one_sided_inverse_basis(4)
        real, imag = self.o.dft_one_sided([1.0, 2.0, 3.0, 4.0], 4)
        with self.assertRaisesRegex(self.o.ISTFTContractError, "denominator"):
            self.o.istft_full(
                [(real, imag)],
                inverse_basis=basis,
                window=[0.0, 1.0, 1.0, 0.0],
                hop_length=4,
            )

    def test_unpadded_hann_tail_fails_closed(self):
        win = self.o.hann_window(8)
        signal = [math.sin(0.2 * i) for i in range(16)]
        frames = self.o.causal_stft_frames(signal, n_fft=8, window=win, hop_length=4, left_pad=4)
        with self.assertRaisesRegex(self.o.ISTFTContractError, "denominator"):
            self.o.istft_full(
                frames,
                inverse_basis=self.o.make_one_sided_inverse_basis(8),
                window=win,
                hop_length=4,
                left_pad=4,
                length=len(signal),
            )

    def test_hop_larger_than_window_is_rejected(self):
        with self.assertRaisesRegex(self.o.ISTFTContractError, "hop_length"):
            self.o.StreamingISTFT(self.o.make_one_sided_inverse_basis(4), [1.0] * 4, 5)

    def test_stream_frame_shape_mismatch_is_rejected(self):
        stream = self.o.StreamingISTFT(self.o.make_one_sided_inverse_basis(4), [1.0] * 4, 2, left_pad=2)
        with self.assertRaisesRegex(self.o.ISTFTContractError, "n_freq mismatch"):
            stream.push([([1.0, 2.0], [0.0, 0.0])])

    def test_push_after_flush_is_rejected_and_second_flush_idempotent(self):
        basis = self.o.make_one_sided_inverse_basis(4)
        frame = self.o.dft_one_sided([1.0, 1.0, 1.0, 1.0], 4)
        stream = self.o.StreamingISTFT(basis, [1.0] * 4, 2, left_pad=2, length=2)
        stream.push([frame])
        stream.flush()
        self.assertEqual(stream.flush(), [])
        with self.assertRaisesRegex(self.o.ISTFTContractError, "after flush"):
            stream.push([frame])

    def test_basis_shape_mismatch_fails(self):
        with self.assertRaisesRegex(self.o.ISTFTContractError, "width"):
            self.o.synthesize_frame([1.0, 2.0], [0.0, 0.0], [[1.0, 2.0]])


if __name__ == "__main__":
    unittest.main()
