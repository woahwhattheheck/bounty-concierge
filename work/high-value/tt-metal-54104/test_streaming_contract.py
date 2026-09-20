#!/usr/bin/env python3
"""Regression tests for the host-only tt-metal #54104 geometry oracle."""

from __future__ import annotations

import unittest

from streaming_contract import (
    CONTRACT,
    acceptance_document,
    centered_istft_default_length,
    centered_stft_frames,
    conv_transpose1d_out,
    geometry,
    overlap_state,
)


class StreamingContractTests(unittest.TestCase):
    def test_reference_rate_identity(self) -> None:
        CONTRACT.validate()
        self.assertEqual(CONTRACT.mel_frame_rate, 50)
        self.assertEqual(CONTRACT.waveform_samples_per_mel_frame, 480)
        self.assertEqual(CONTRACT.reference_samples_per_mel_frame, 480)
        self.assertEqual(CONTRACT.spectral_bins, 9)
        self.assertEqual(CONTRACT.synthesis_head_channels, 18)

    def test_one_mel_frame_exact_stage_alignment(self) -> None:
        g = geometry(1)
        self.assertEqual(g.source_samples, 480)
        self.assertEqual(g.source_stft_frames, 121)
        self.assertEqual(g.output_samples, 480)
        self.assertEqual(
            [s.decoder_after_upsample for s in g.stages],
            [8, 40, 120],
        )
        self.assertEqual(
            [s.decoder_at_fusion for s in g.stages],
            [8, 40, 121],
        )
        self.assertEqual(
            [s.source_at_fusion for s in g.stages],
            [8, 40, 121],
        )
        self.assertEqual([s.source_stride for s in g.stages], [15, 3, 1])

    def test_multiple_lengths_preserve_alignment_and_exact_output(self) -> None:
        for mel_frames in (1, 2, 3, 7, 31, 50, 63, 127):
            with self.subTest(mel_frames=mel_frames):
                g = geometry(mel_frames)
                self.assertEqual(g.output_samples, mel_frames * 480)
                for stage in g.stages:
                    self.assertEqual(stage.decoder_at_fusion, stage.source_at_fusion)

    def test_reference_transposed_convs_are_exact_integer_upsamples(self) -> None:
        length = 13
        for stride, kernel in zip(CONTRACT.upsample_rates, CONTRACT.upsample_kernel_sizes):
            padding = (kernel - stride) // 2
            out = conv_transpose1d_out(
                length, stride=stride, kernel=kernel, padding=padding
            )
            self.assertEqual(out, length * stride)
            length = out

    def test_centered_stft_and_istft_frame_relation(self) -> None:
        for mel_frames in (1, 7, 50):
            samples = mel_frames * 480
            frames = centered_stft_frames(samples, n_fft=16, hop=4)
            self.assertEqual(frames, mel_frames * 120 + 1)
            self.assertEqual(
                centered_istft_default_length(frames, hop=4),
                samples,
            )

    def test_reference_chunk_is_one_second(self) -> None:
        doc = acceptance_document()
        chunk = doc["canonical_chunk"]
        self.assertEqual(chunk["tokens"], 25)
        self.assertEqual(chunk["mel_frames"], 50)
        self.assertEqual(chunk["output_samples"], 24_000)
        self.assertEqual(chunk["seconds_at_24khz"], 1.0)

    def test_overlap_add_state_lower_bound(self) -> None:
        state = overlap_state()
        self.assertEqual(state["overlap_samples"], 12)
        self.assertEqual(state["prior_spectral_frames"], 3)
        self.assertEqual(state["concurrent_window_count"], 4)
        self.assertEqual(state["center_trim_samples_each_side"], 8)

    def test_invalid_lengths_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            geometry(0)
        with self.assertRaises(ValueError):
            centered_stft_frames(0, n_fft=16, hop=4)
        with self.assertRaises(ValueError):
            centered_istft_default_length(0, hop=4)

    def test_fixture_document_contains_stage_identity(self) -> None:
        doc = acceptance_document()
        self.assertEqual(len(doc["canonical_fixtures"]), 5)
        for fixture in doc["canonical_fixtures"]:
            self.assertEqual(fixture["output_samples"], fixture["mel_frames"] * 480)
            for stage in fixture["stages"]:
                self.assertEqual(stage["decoder_at_fusion"], stage["source_at_fusion"])


if __name__ == "__main__":
    unittest.main()
