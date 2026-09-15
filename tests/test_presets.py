import random
from collections.abc import Sequence

import pytest

from localsr.core.model_catalog import (
    MODEL_CATALOG,
    CatalogModel,
    ModelPurpose,
    QualityTier,
    SpeedTier,
)
from localsr.core.presets import (
    NoCompatibleModelError,
    Preset,
    PresetMode,
    _balanced_score,
    _device_priority,
    _mode_category,
    _purpose_match,
    get_preset_model,
    rank_models_for_preset,
    resolve_settings_for_model,
    select_model_for_preset,
)


def _make_dummy_model(
    model_id: str,
    native_scale: int = 4,
    purposes: Sequence[ModelPurpose | str] = (ModelPurpose.GENERAL,),
    quality_tier: QualityTier = QualityTier.STANDARD,
    speed_tier: SpeedTier = SpeedTier.MEDIUM,
    memory_factor: float = 1.0,
    time_factor: float = 1.0,
    speed_factor: float = 0.5,
    recommended_halo: int = 16,
    size_bytes: int = 10_000_000,
) -> CatalogModel:
    return CatalogModel(
        model_id=model_id,
        name=f"Model {model_id}",
        filename=f"{model_id}.pth",
        description="Test model",
        size_bytes=size_bytes,
        sha256="0" * 64,
        download_url=f"https://example.test/{model_id}.pth",
        native_scale=native_scale,
        purposes=tuple(ModelPurpose(p) if isinstance(p, str) else p for p in purposes),
        quality_tier=quality_tier,
        speed_tier=speed_tier,
        memory_factor=memory_factor,
        time_factor=time_factor,
        speed_factor=speed_factor,
        recommended_halo=recommended_halo,
    )


class TestPurposeMatch:
    def test_exact_purpose_match_returns_2(self):
        m = _make_dummy_model("m1", purposes=(ModelPurpose.PHOTO,))
        assert _purpose_match(m, ModelPurpose.PHOTO) == 2
        assert _purpose_match(m, "photo") == 2

    def test_alias_purposes_match_returns_2(self):
        m_anime = _make_dummy_model("m_anime", purposes=(ModelPurpose.ANIME,))
        assert _purpose_match(m_anime, ModelPurpose.ILLUSTRATION) == 2
        assert _purpose_match(m_anime, "illustration") == 2

        m_deblur = _make_dummy_model("m_deblur", purposes=(ModelPurpose.DEBLUR,))
        assert _purpose_match(m_deblur, ModelPurpose.RESTORATION) == 2
        assert _purpose_match(m_deblur, "restoration") == 2

    def test_general_purpose_match_returns_1(self):
        m = _make_dummy_model("m1", purposes=(ModelPurpose.GENERAL,))
        assert _purpose_match(m, ModelPurpose.PHOTO) == 1
        assert _purpose_match(m, ModelPurpose.ILLUSTRATION) == 1
        assert _purpose_match(m, ModelPurpose.DENOISE) == 1

    def test_general_purpose_for_general_request_returns_2(self):
        m = _make_dummy_model("m1", purposes=(ModelPurpose.GENERAL,))
        assert _purpose_match(m, ModelPurpose.GENERAL) == 2

    def test_no_match_returns_0(self):
        m = _make_dummy_model("m1", purposes=(ModelPurpose.DENOISE,))
        assert _purpose_match(m, ModelPurpose.PHOTO) == 0
        assert _purpose_match(m, ModelPurpose.ILLUSTRATION) == 0

    def test_exact_overrides_general_when_both_present(self):
        m = _make_dummy_model("m1", purposes=(ModelPurpose.GENERAL, ModelPurpose.PHOTO))
        assert _purpose_match(m, ModelPurpose.PHOTO) == 2


class TestModeCategory:
    def test_all_preset_modes_map_to_categories(self):
        assert _mode_category(Preset.FAST) == "fast"
        assert _mode_category(PresetMode.QUICK_UPSCALE) == "fast"
        assert _mode_category(PresetMode.QUICK_DENOISE) == "fast"
        assert _mode_category("fast") == "fast"

        assert _mode_category(Preset.BALANCED) == "balanced"
        assert _mode_category("balanced") == "balanced"

        assert _mode_category(Preset.QUALITY) == "quality"
        assert _mode_category("quality") == "quality"

        assert _mode_category(Preset.ULTRA) == "ultra"
        assert _mode_category(PresetMode.BEST_UPSCALE) == "ultra"
        assert _mode_category(PresetMode.BEST_DENOISE) == "ultra"
        assert _mode_category(PresetMode.COMBO) == "ultra"
        assert _mode_category("ultra") == "ultra"


class TestPresetRankingFormulas:
    def test_fast_preset_prioritizes_speed_speedfactor_memory_size_installed(self):
        m1 = _make_dummy_model(
            "m1",
            speed_tier=SpeedTier.FAST,
            speed_factor=0.9,
            memory_factor=0.5,
            size_bytes=5_000_000,
        )
        m2 = _make_dummy_model(
            "m2",
            speed_tier=SpeedTier.FAST,
            speed_factor=0.5,
            memory_factor=0.5,
            size_bytes=5_000_000,
        )
        m3 = _make_dummy_model(
            "m3",
            speed_tier=SpeedTier.MEDIUM,
            speed_factor=0.9,
            memory_factor=0.2,
            size_bytes=1_000_000,
        )

        ranked = rank_models_for_preset([m3, m2, m1], Preset.FAST)
        assert [m.model_id for m in ranked] == ["m1", "m2", "m3"]

    def test_balanced_score_and_ranking(self):
        m_heavy_slow = _make_dummy_model(
            "m_heavy_slow",
            quality_tier=QualityTier.MAXIMUM,
            speed_tier=SpeedTier.SLOW,
            speed_factor=0.3,
            memory_factor=1.8,
        )
        m_efficient_high = _make_dummy_model(
            "m_efficient_high",
            quality_tier=QualityTier.HIGH,
            speed_tier=SpeedTier.FAST,
            speed_factor=0.85,
            memory_factor=0.4,
        )

        score_heavy = _balanced_score(m_heavy_slow)
        score_eff = _balanced_score(m_efficient_high)
        assert score_eff > score_heavy

        ranked = rank_models_for_preset([m_heavy_slow, m_efficient_high], Preset.BALANCED)
        assert ranked[0].model_id == "m_efficient_high"

    def test_quality_preset_prioritizes_quality_then_speed_factor(self):
        m_q_fast = _make_dummy_model(
            "m_q_fast",
            quality_tier=QualityTier.HIGH,
            speed_factor=0.8,
        )
        m_q_slow = _make_dummy_model(
            "m_q_slow",
            quality_tier=QualityTier.HIGH,
            speed_factor=0.3,
        )

        ranked = rank_models_for_preset([m_q_slow, m_q_fast], Preset.QUALITY)
        assert ranked[0].model_id == "m_q_fast"

    def test_ultra_preset_prioritizes_quality_then_size_then_speed(self):
        m_large = _make_dummy_model(
            "m_large",
            quality_tier=QualityTier.MAXIMUM,
            size_bytes=150_000_000,
            speed_tier=SpeedTier.SLOW,
        )
        m_compact = _make_dummy_model(
            "m_compact",
            quality_tier=QualityTier.MAXIMUM,
            size_bytes=20_000_000,
            speed_tier=SpeedTier.MEDIUM,
        )

        ranked = rank_models_for_preset([m_compact, m_large], Preset.ULTRA)
        assert ranked[0].model_id == "m_large"

    def test_scale_filtering_and_auto_scale_inference(self):
        m_scale1 = _make_dummy_model("m_scale1", native_scale=1, purposes=(ModelPurpose.DENOISE,))
        m_scale4 = _make_dummy_model("m_scale4", native_scale=4, purposes=(ModelPurpose.PHOTO,))

        # Without explicit output_scale, deblur/denoise infer scale 1, photo infers scale 4
        ranked_denoise = rank_models_for_preset(
            [m_scale1, m_scale4], Preset.FAST, purpose=ModelPurpose.DENOISE
        )
        assert ranked_denoise[0].model_id == "m_scale1"

        ranked_photo = rank_models_for_preset(
            [m_scale1, m_scale4], Preset.FAST, purpose=ModelPurpose.PHOTO
        )
        assert ranked_photo[0].model_id == "m_scale4"

    def test_output_scale_less_than_1_raises_value_error(self):
        m = _make_dummy_model("m1")
        with pytest.raises(ValueError, match="Output scale must be at least 1"):
            rank_models_for_preset([m], PresetMode.QUICK_UPSCALE, output_scale=0)


class TestStandardCatalogRankings:
    def test_photo_fast_preset_selects_span_photo_x4(self):
        selected = get_preset_model(Preset.FAST, ModelPurpose.PHOTO)
        assert selected.model_id == "span_photo_x4"

        selected_quick = select_model_for_preset(
            MODEL_CATALOG, PresetMode.QUICK_UPSCALE, purpose=ModelPurpose.PHOTO
        )
        assert selected_quick.model_id == "span_photo_x4"

    def test_photo_ultra_preset_selects_realplksr_nomos8k(self):
        selected = get_preset_model(Preset.ULTRA, ModelPurpose.PHOTO)
        assert selected.model_id == "realplksr_nomoswebphoto_x4"

        selected_best = select_model_for_preset(
            MODEL_CATALOG, PresetMode.BEST_UPSCALE, purpose=ModelPurpose.PHOTO
        )
        assert selected_best.model_id == "realplksr_nomoswebphoto_x4"

    def test_photo_quality_preset_selects_realplksr(self):
        selected = get_preset_model(Preset.QUALITY, ModelPurpose.PHOTO)
        assert selected.model_id == "realplksr_nomoswebphoto_x4"

    def test_illustration_fast_preset_selects_the_compact_anime_model(self):
        # Real-ESRGAN Anime 6B is the fastest verified illustration checkpoint;
        # HFA2k stays the Best pick (same quality tier, larger model).
        selected = get_preset_model(Preset.FAST, ModelPurpose.ILLUSTRATION)
        assert selected.model_id == "realesrgan_x4plus_anime_6b"
        assert get_preset_model(Preset.ULTRA, ModelPurpose.ILLUSTRATION).model_id == (
            "realplksr_hfa2k_anime_x4"
        )

    def test_illustration_via_string_anime_selects_the_compact_anime_model(self):
        selected = get_preset_model(Preset.FAST, "anime")
        assert selected.model_id == "realesrgan_x4plus_anime_6b"

    def test_face_quick_selects_hat_s_x4_face(self):
        selected = select_model_for_preset(
            MODEL_CATALOG,
            PresetMode.QUICK_UPSCALE,
            purpose=ModelPurpose.FACE,
            output_scale=4,
        )
        assert selected.model_id == "hat_s_x4_face"

    def test_denoise_quick_selects_realplksr(self):
        selected = select_model_for_preset(
            MODEL_CATALOG,
            PresetMode.QUICK_DENOISE,
            purpose=ModelPurpose.DENOISE,
            output_scale=1,
        )
        assert selected.model_id == "denoise_realplksr_1x"

    def test_denoise_best_selects_nafnet(self):
        selected = select_model_for_preset(
            MODEL_CATALOG,
            PresetMode.BEST_DENOISE,
            purpose=ModelPurpose.DENOISE,
            output_scale=1,
        )
        assert selected.model_id == "nafnet_sidd_width64"

    def test_deblur_selects_nafnet_gopro_deblur(self):
        selected = get_preset_model(Preset.FAST, ModelPurpose.DEBLUR)
        assert selected.model_id == "nafnet_gopro_deblur"

        selected_restoration = get_preset_model(Preset.QUALITY, "restoration")
        assert selected_restoration.model_id == "nafnet_gopro_deblur"


class TestEdgeCases:
    def test_invalid_or_unmatched_purpose_returns_empty_when_no_general(self):
        m_denoise_only = _make_dummy_model("m_denoise", purposes=(ModelPurpose.DENOISE,))
        ranked = rank_models_for_preset(
            [m_denoise_only],
            PresetMode.QUICK_UPSCALE,
            purpose=ModelPurpose.FACE,
            output_scale=4,
        )
        assert ranked == ()

        with pytest.raises(NoCompatibleModelError, match="No compatible model"):
            select_model_for_preset(
                [m_denoise_only],
                PresetMode.QUICK_UPSCALE,
                purpose=ModelPurpose.FACE,
                output_scale=4,
            )

    def test_unmatched_purpose_falls_back_to_general_model(self):
        m_gen = _make_dummy_model("m_gen", native_scale=4, purposes=(ModelPurpose.GENERAL,))
        ranked = rank_models_for_preset(
            [m_gen],
            PresetMode.QUICK_UPSCALE,
            purpose=ModelPurpose.FACE,
            output_scale=4,
        )
        assert len(ranked) == 1
        assert ranked[0].model_id == "m_gen"

    def test_empty_catalog(self):
        for mode in PresetMode:
            assert rank_models_for_preset([], mode) == ()
            with pytest.raises(NoCompatibleModelError):
                select_model_for_preset([], mode)

        for preset in Preset:
            assert rank_models_for_preset([], preset) == ()
            with pytest.raises(NoCompatibleModelError):
                select_model_for_preset([], preset)

    def test_rank_stability_across_shuffled_inputs(self):
        models = [
            _make_dummy_model(f"m_{i:02d}", speed_tier=SpeedTier.FAST, memory_factor=0.5)
            for i in range(25)
        ]
        expected_order = tuple(sorted(models, key=lambda m: m.model_id))

        for seed in range(50):
            shuffled = list(models)
            random.Random(seed).shuffle(shuffled)
            result = rank_models_for_preset(shuffled, PresetMode.QUICK_UPSCALE)
            assert result == expected_order

    def test_rank_stability_on_repeated_invocations(self):
        for mode in PresetMode:
            res1 = rank_models_for_preset(MODEL_CATALOG, mode, output_scale=1)
            res2 = rank_models_for_preset(MODEL_CATALOG, mode, output_scale=1)
            res3 = rank_models_for_preset(MODEL_CATALOG, mode, output_scale=1)
            assert res1 == res2 == res3

        for preset in Preset:
            res1 = rank_models_for_preset(MODEL_CATALOG, preset, output_scale=1)
            res2 = rank_models_for_preset(MODEL_CATALOG, preset, output_scale=1)
            res3 = rank_models_for_preset(MODEL_CATALOG, preset, output_scale=1)
            assert res1 == res2 == res3

    def test_property_based_random_catalog_stress(self):
        rng = random.Random(42)
        purposes = list(ModelPurpose)
        qualities = list(QualityTier)
        speeds = list(SpeedTier)

        for _ in range(20):
            models = []
            for i in range(15):
                m = _make_dummy_model(
                    model_id=f"synth_{i:02d}",
                    native_scale=rng.choice([1, 2, 4, 8]),
                    purposes=rng.sample(purposes, k=rng.randint(1, 3)),
                    quality_tier=rng.choice(qualities),
                    speed_tier=rng.choice(speeds),
                    memory_factor=round(rng.uniform(0.1, 2.5), 2),
                    speed_factor=round(rng.uniform(0.1, 1.0), 2),
                    size_bytes=rng.randint(1_000_000, 500_000_000),
                )
                models.append(m)

            for mode in (Preset.FAST, Preset.BALANCED, Preset.QUALITY, Preset.ULTRA):
                target_purpose = rng.choice(purposes)
                scale = rng.choice([1, 2, 4])
                ranked = rank_models_for_preset(
                    models,
                    mode,
                    purpose=target_purpose,
                    output_scale=scale,
                )
                assert all(m.native_scale >= scale for m in ranked)
                assert all(_purpose_match(m, target_purpose) > 0 for m in ranked)


class TestDevicePriorityAndSettingsResolution:
    @pytest.mark.parametrize("mode", [PresetMode.QUICK_UPSCALE, PresetMode.BEST_UPSCALE])
    def test_presets_use_an_available_intel_igpu_before_cpu(self, mode):
        gib = 1024**3
        model = _make_dummy_model("m1")
        settings = resolve_settings_for_model(
            model=model,
            mode=mode,
            devices=[
                {
                    "id": "cpu",
                    "type": "cpu",
                    "name": "CPU",
                    "free_memory": 8 * gib,
                    "supports_fp16": False,
                    "recommended_tile_sizes": [64, 128],
                },
                {
                    "id": "directml:1",
                    "type": "directml",
                    "name": "Intel(R) Iris(R) Xe Graphics (DirectML)",
                    "free_memory": 2 * gib,
                    "supports_fp16": True,
                    "recommended_tile_sizes": [64, 128],
                    "is_integrated": True,
                },
            ],
            image_width=128,
            image_height=96,
            output_scale=4,
            available_system_memory=8 * gib,
            available_disk=10 * gib,
            model_half_supported=True,
        )
        assert settings.device_id == "directml:1"
        assert settings.device_type == "directml"
        assert settings.safe_memory is True
        assert not settings.estimate.blocking
        assert settings.precision == ("fp16" if mode == PresetMode.QUICK_UPSCALE else "fp32")

    def test_device_priority_ordering(self):
        cuda_dev = {"id": "cuda:0", "type": "cuda", "free_memory": 4 * 1024**3}
        rocm_dev = {"id": "rocm:0", "type": "rocm", "free_memory": 4 * 1024**3}
        mps_dev = {"id": "mps", "type": "mps", "free_memory": 8 * 1024**3}
        xpu_dev = {"id": "xpu:0", "type": "xpu", "free_memory": 6 * 1024**3}
        cpu_dev = {"id": "cpu", "type": "cpu", "free_memory": 16 * 1024**3}
        unknown_dev = {"id": "other", "type": "custom", "free_memory": 32 * 1024**3}

        assert _device_priority(cuda_dev)[0] == 5
        assert _device_priority(rocm_dev)[0] == 5
        assert _device_priority(mps_dev)[0] == 4
        assert _device_priority(xpu_dev)[0] == 3
        assert _device_priority(cpu_dev)[0] == 1
        assert _device_priority(unknown_dev)[0] == 0

    def test_resolve_settings_raises_on_invalid_image_dimensions(self):
        m = _make_dummy_model("m1", native_scale=4)
        with pytest.raises(ValueError, match="An image must be selected"):
            resolve_settings_for_model(
                model=m,
                mode=PresetMode.QUICK_UPSCALE,
                devices=[{"id": "cpu", "type": "cpu"}],
                image_width=0,
                image_height=100,
                output_scale=4,
                available_system_memory=8 * 1024**3,
                available_disk=10 * 1024**3,
                model_half_supported=True,
            )

        with pytest.raises(ValueError, match="An image must be selected"):
            resolve_settings_for_model(
                model=m,
                mode=PresetMode.QUICK_UPSCALE,
                devices=[{"id": "cpu", "type": "cpu"}],
                image_width=100,
                image_height=-5,
                output_scale=4,
                available_system_memory=8 * 1024**3,
                available_disk=10 * 1024**3,
                model_half_supported=True,
            )

    def test_resolve_settings_raises_on_invalid_output_scale(self):
        m = _make_dummy_model("m1", native_scale=4)
        with pytest.raises(ValueError, match="incompatible with the selected model"):
            resolve_settings_for_model(
                model=m,
                mode=PresetMode.QUICK_UPSCALE,
                devices=[{"id": "cpu", "type": "cpu"}],
                image_width=100,
                image_height=100,
                output_scale=5,
                available_system_memory=8 * 1024**3,
                available_disk=10 * 1024**3,
                model_half_supported=True,
            )

        with pytest.raises(ValueError, match="incompatible with the selected model"):
            resolve_settings_for_model(
                model=m,
                mode=PresetMode.QUICK_UPSCALE,
                devices=[{"id": "cpu", "type": "cpu"}],
                image_width=100,
                image_height=100,
                output_scale=0,
                available_system_memory=8 * 1024**3,
                available_disk=10 * 1024**3,
                model_half_supported=True,
            )

    def test_resolve_settings_raises_on_no_devices(self):
        m = _make_dummy_model("m1", native_scale=4)
        with pytest.raises(ValueError, match="No compute device is available"):
            resolve_settings_for_model(
                model=m,
                mode=PresetMode.QUICK_UPSCALE,
                devices=[],
                image_width=100,
                image_height=100,
                output_scale=4,
                available_system_memory=8 * 1024**3,
                available_disk=10 * 1024**3,
                model_half_supported=True,
            )

    def test_low_memory_enables_safe_memory_and_rotates_tile_sizes(self):
        gib = 1024**3
        m = _make_dummy_model("m1", native_scale=4, size_bytes=50 * 1024**2)
        device = {
            "id": "cuda:0",
            "type": "cuda",
            "free_memory": int(1.5 * gib),
            "supports_fp16": True,
            "recommended_tile_sizes": [64, 128, 256],
        }
        settings = resolve_settings_for_model(
            model=m,
            mode=PresetMode.QUICK_UPSCALE,
            devices=[device],
            image_width=1000,
            image_height=1000,
            output_scale=4,
            available_system_memory=8 * gib,
            available_disk=10 * gib,
            model_half_supported=True,
        )
        assert settings.safe_memory is True
        assert settings.tile_size == 128

    def test_cpu_never_selects_fp16_even_if_supports_fp16_is_true(self):
        gib = 1024**3
        m = _make_dummy_model("m1", native_scale=4, size_bytes=50 * 1024**2)
        device = {
            "id": "cpu",
            "type": "cpu",
            "free_memory": 16 * gib,
            "supports_fp16": True,
            "recommended_tile_sizes": [64],
        }
        settings = resolve_settings_for_model(
            model=m,
            mode=PresetMode.QUICK_UPSCALE,
            devices=[device],
            image_width=500,
            image_height=500,
            output_scale=4,
            available_system_memory=16 * gib,
            available_disk=10 * gib,
            model_half_supported=True,
        )
        assert settings.precision == "fp32"
