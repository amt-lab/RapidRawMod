use crate::file_management::{parse_virtual_path, read_file_mapped};
use crate::image_loader::load_base_image_from_bytes;
use base64::{Engine as _, engine::general_purpose};
use image::codecs::jpeg::JpegEncoder;
use image::{DynamicImage, Rgb32FImage};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::Cursor;
use std::path::Path;
use tauri::AppHandle;

use crate::AppState;
use crate::image_processing::downscale_f32_image;
use crate::load_settings;
use tauri::Emitter;

// Pseudo-log post-gamma (ported from neg-invert; mimics log tone redistribution).
const PSL_GAMMA: f32 = 1.6;

// Auto black/white point percentiles, as fractions of the sorted samples.
// 0.001 = 0.1st percentile (low end -> black point base);
// 0.999 = 99.9th percentile (high end -> white point base).
// Symmetric 0.1% in from each end. Tweak here to change the auto stretch.

//const PCT_LOW: f32 = 0.001;
//const PCT_HIGH: f32 = 0.999;

const PCT_LOW: f32 = 0.05;
const PCT_HIGH: f32 = 0.995;


// Fraction of width/height trimmed from EACH side before percentile analysis.
// 0.12 -> central 76% region (100% - 12% - 12%). Raise to ignore more of the
// borders (film edges/sprockets), lower to analyze more of the frame.
const CENTER_MARGIN: f32 = 0.12;

// Conversion mode = which working space the invert+stretch happens in.
// See docs RapidRaw-negative-conversion-modes.md.
#[derive(Serialize, Deserialize, Debug, Clone, Copy, PartialEq, Default)]
#[serde(rename_all = "lowercase")]
pub enum ConversionMode {
    Lin, // linear inversion: work = 1 - v
    Psl, // pseudo-log: linear inversion, then work^PSL_GAMMA after the stretch
    #[default]
    Log, // optical density: work = -log10(v)
}

// Forward transform: scanned-negative pixel -> working space (per channel).
#[inline]
fn to_working(v: f32, mode: ConversionMode) -> f32 {
    match mode {
        ConversionMode::Log => -v.clamp(1e-6, 1.0).log10(),
        _ => 1.0 - v.clamp(0.0, 1.0), // lin and psl share linear inversion
    }
}

// Inverse transform: working-space value -> negative pixel value (for the readout).
#[inline]
fn from_working(d: f32, mode: ConversionMode) -> f32 {
    match mode {
        ConversionMode::Log => 10f32.powf(-d),
        _ => 1.0 - d,
    }
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy)]
pub struct NegativeConversionParams {
    pub red_weight: f32,
    pub green_weight: f32,
    pub blue_weight: f32,

    pub exposure: f32,
    pub contrast: f32,
    pub gamma: f32,

    #[serde(default)]
    pub mode: ConversionMode,

    // Black/white point picking (ported from neg-invert). Overrides are per-channel
    // values in density working space (-log10(px)); None = use auto percentile.
    // Tweaks slide each endpoint along the black->white axis by tweak * range.
    #[serde(default)]
    pub bp_override: Option<[f32; 3]>,
    #[serde(default)]
    pub wp_override: Option<[f32; 3]>,
    #[serde(default)]
    pub bp_tweak: f32,
    #[serde(default)]
    pub wp_tweak: f32,
}

impl Default for NegativeConversionParams {
    fn default() -> Self {
        Self {
            red_weight: 1.0,
            green_weight: 1.0,
            blue_weight: 1.0,
            exposure: 0.0,
            contrast: 1.0,
            gamma: 2.2,
            mode: ConversionMode::Log,
            bp_override: None,
            wp_override: None,
            bp_tweak: 0.0,
            wp_tweak: 0.0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct ChannelBounds {
    pub min: f32,
    pub max: f32,
}

#[derive(Serialize, Clone)]
pub struct NegativePreviewResult {
    pub image: String,
    // Negative pixel values [0,255] at the auto-selected endpoints.
    // black_point maps to output black, white_point maps to output white.
    pub black_point: [u16; 3],
    pub white_point: [u16; 3],
    // Fraction trimmed from each side for percentile analysis (== CENTER_MARGIN),
    // sent so the GUI can draw the analysis region without hardcoding it.
    pub center_margin: f32,
}

// Resolve the effective per-channel black/white points (density working space)
// the pipeline actually uses: start from a click override or the auto percentile
// bounds, then slide each endpoint along the black->white axis by tweak * range.
// (Ported from neg-invert `_run_stretch`; direction of the axis is preserved.)
fn resolve_points(bounds: &[ChannelBounds; 3], params: &NegativeConversionParams) -> ([f32; 3], [f32; 3]) {
    let mut bp_base = [bounds[0].min, bounds[1].min, bounds[2].min];
    let mut wp_base = [bounds[0].max, bounds[1].max, bounds[2].max];
    if let Some(o) = params.bp_override {
        bp_base = o;
    }
    if let Some(o) = params.wp_override {
        wp_base = o;
    }

    let mut bp = [0.0f32; 3];
    let mut wp = [0.0f32; 3];
    for c in 0..3 {
        let rng = (wp_base[c] - bp_base[c]).max(1e-6);
        bp[c] = bp_base[c] + params.bp_tweak * rng;
        wp[c] = wp_base[c] + params.wp_tweak * rng;
    }
    (bp, wp)
}

// Convert the density-space endpoints back to negative pixel values in the
// standard [0,255] range, split into the two output roles.
fn points_to_display_255(bp: &[f32; 3], wp: &[f32; 3], mode: ConversionMode) -> ([u16; 3], [u16; 3]) {
    let to_255 = |d: f32| -> u16 {
        let v = from_working(d, mode); // back to negative pixel value
        (v * 255.0).round().clamp(0.0, 255.0) as u16
    };

    let mut black = [0u16; 3];
    let mut white = [0u16; 3];
    for c in 0..3 {
        // bp (low density / thin film) -> output BLACK
        black[c] = to_255(bp[c]);
        // wp (high density / dense film) -> output WHITE
        white[c] = to_255(wp[c]);
    }
    (black, white)
}

fn analyze_bounds(log_data: &[f32], width: usize, height: usize) -> [ChannelBounds; 3] {
    let margin_x = (width as f32 * CENTER_MARGIN) as usize;
    let margin_y = (height as f32 * CENTER_MARGIN) as usize;

    let est_pixels = (width.saturating_sub(margin_x * 2)) * (height.saturating_sub(margin_y * 2));
    let step = (est_pixels / 40_000).max(1);

    let mut r_vals = Vec::with_capacity(est_pixels / step);
    let mut g_vals = Vec::with_capacity(est_pixels / step);
    let mut b_vals = Vec::with_capacity(est_pixels / step);

    for y in (margin_y..(height - margin_y)).step_by(3) {
        let row_offset = y * width * 3;

        for x in (margin_x..(width - margin_x)).step_by(step) {
            let idx = row_offset + (x * 3);

            if idx + 2 < log_data.len() {
                let r = log_data[idx];
                let g = log_data[idx + 1];
                let b = log_data[idx + 2];

                if r.is_finite() {
                    r_vals.push(r);
                }
                if g.is_finite() {
                    g_vals.push(g);
                }
                if b.is_finite() {
                    b_vals.push(b);
                }
            }
        }
    }

    let get_bounds = |mut vals: Vec<f32>| -> ChannelBounds {
        if vals.is_empty() {
            return ChannelBounds { min: 0.0, max: 1.0 };
        }

        vals.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));

        let len = vals.len() as f32;

        let min_idx = (len * PCT_LOW) as usize;
        let max_idx = (len * PCT_HIGH) as usize;

        let min = vals[min_idx.min(vals.len().saturating_sub(1))];
        let max = vals[max_idx.min(vals.len().saturating_sub(1))];

        let safe_max = if max <= min + 0.0001 { min + 1.0 } else { max };

        ChannelBounds { min, max: safe_max }
    };

    [get_bounds(r_vals), get_bounds(g_vals), get_bounds(b_vals)]
}

fn run_pipeline(
    input: &DynamicImage,
    params: &NegativeConversionParams,
    override_bounds: Option<[ChannelBounds; 3]>,
) -> DynamicImage {
    let rgb = input.to_rgb32f();
    let (width, height) = rgb.dimensions();
    let raw_pixels = rgb.as_raw();

    let mode = params.mode;
    let log_pixels: Vec<f32> = raw_pixels
        .par_iter()
        .map(|&v| to_working(v, mode))
        .collect();

    let bounds = if let Some(b) = override_bounds {
        b
    } else {
        analyze_bounds(&log_pixels, width as usize, height as usize)
    };

    let (bp, wp) = resolve_points(&bounds, params);
    let psl = mode == ConversionMode::Psl;

    let mut out_buffer = vec![0.0f32; raw_pixels.len()];

    let k = 4.0 * params.contrast.max(0.1);
    let x0 = 0.6 - (params.exposure * 0.25);
    let gamma_inv = 1.0 / params.gamma.max(0.01);

    let y0 = 1.0 / (1.0 + (k * x0).exp());
    let y1 = 1.0 / (1.0 + (-k * (1.0 - x0)).exp());
    let scale = 1.0 / (y1 - y0);

    out_buffer
        .par_chunks_mut(3)
        .enumerate()
        .for_each(|(i, out_pixel)| {
            let idx = i * 3;

            let mut n_r = (log_pixels[idx] - bp[0]) / (wp[0] - bp[0]).max(1e-6);
            let mut n_g = (log_pixels[idx + 1] - bp[1]) / (wp[1] - bp[1]).max(1e-6);
            let mut n_b = (log_pixels[idx + 2] - bp[2]) / (wp[2] - bp[2]).max(1e-6);

            n_r = n_r.max(0.0);
            n_g = n_g.max(0.0);
            n_b = n_b.max(0.0);

            // PSL: pseudo-log gamma applied to the normalized stretch result,
            // mirroring neg-invert's post-stretch power. (lin/log: no-op.)
            if psl {
                n_r = n_r.powf(PSL_GAMMA);
                n_g = n_g.powf(PSL_GAMMA);
                n_b = n_b.powf(PSL_GAMMA);
            }

            n_r *= params.red_weight;
            n_g *= params.green_weight;
            n_b *= params.blue_weight;

            let apply_curve = |x: f32| -> f32 {
                let sigmoid = 1.0 / (1.0 + (-k * (x - x0)).exp());
                let s_norm = (sigmoid - y0) * scale;
                s_norm.clamp(0.0, 1.0)
            };

            let mut r = apply_curve(n_r);
            let mut g = apply_curve(n_g);
            let mut b = apply_curve(n_b);

            let luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
            let max_ch = r.max(g).max(b);

            if max_ch > 0.9 {
                let overflow = ((max_ch - 0.9) * 10.0).clamp(0.0, 1.0);
                let sat_reduction = overflow * overflow;

                r = r + (luma - r) * sat_reduction;
                g = g + (luma - g) * sat_reduction;
                b = b + (luma - b) * sat_reduction;
            }

            out_pixel[0] = r.clamp(0.0, 1.0).powf(gamma_inv);
            out_pixel[1] = g.clamp(0.0, 1.0).powf(gamma_inv);
            out_pixel[2] = b.clamp(0.0, 1.0).powf(gamma_inv);
        });

    let out_img = Rgb32FImage::from_vec(width, height, out_buffer).unwrap();
    DynamicImage::ImageRgb32F(out_img)
}

#[tauri::command]
pub async fn preview_negative_conversion(
    path: String,
    params: NegativeConversionParams,
    state: tauri::State<'_, AppState>,
    app_handle: AppHandle,
) -> Result<NegativePreviewResult, String> {
    let (source_path, _) = parse_virtual_path(&path);
    let source_path_str = source_path.to_string_lossy().to_string();

    let mut hasher = DefaultHasher::new();
    source_path_str.hash(&mut hasher);
    "negative_preview_base".hash(&mut hasher);
    let cache_key = hasher.finish();

    let base_image_for_processing = {
        let mut cache = state.geometry_cache.lock().unwrap();

        if let Some(cached_img) = cache.get(&cache_key) {
            cached_img.clone()
        } else {
            let image_to_downscale = {
                let original_lock = state.original_image.lock().unwrap();
                if let Some(loaded) = original_lock.as_ref() {
                    if loaded.path == source_path_str {
                        loaded.image.clone().as_ref().clone()
                    } else {
                        drop(original_lock);
                        let settings = load_settings(app_handle.clone()).unwrap_or_default();

                        match read_file_mapped(Path::new(&source_path_str)) {
                            Ok(mmap) => load_base_image_from_bytes(
                                &mmap,
                                &source_path_str,
                                false,
                                &settings,
                                None,
                            )
                            .map_err(|e| e.to_string())?,
                            Err(_e) => {
                                let bytes = fs::read(&source_path_str)
                                    .map_err(|io_err| io_err.to_string())?;
                                load_base_image_from_bytes(
                                    &bytes,
                                    &source_path_str,
                                    false,
                                    &settings,
                                    None,
                                )
                                .map_err(|e| e.to_string())?
                            }
                        }
                    }
                } else {
                    drop(original_lock);
                    let settings = load_settings(app_handle.clone()).unwrap_or_default();

                    match read_file_mapped(Path::new(&source_path_str)) {
                        Ok(mmap) => load_base_image_from_bytes(
                            &mmap,
                            &source_path_str,
                            false,
                            &settings,
                            None,
                        )
                        .map_err(|e| e.to_string())?,
                        Err(_e) => {
                            let bytes =
                                fs::read(&source_path_str).map_err(|io_err| io_err.to_string())?;
                            load_base_image_from_bytes(
                                &bytes,
                                &source_path_str,
                                false,
                                &settings,
                                None,
                            )
                            .map_err(|e| e.to_string())?
                        }
                    }
                }
            };

            let downscaled = downscale_f32_image(&image_to_downscale, 1080, 1080);

            cache.insert(cache_key, downscaled.clone());
            downscaled
        }
    };

    // Compute the auto black/white points (percentiles in log/density space)
    // up front so they drive the pipeline AND get reported back to the GUI.
    let rgb = base_image_for_processing.to_rgb32f();
    let (w, h) = rgb.dimensions();
    let log_pixels: Vec<f32> = rgb
        .as_raw()
        .par_iter()
        .map(|&v| to_working(v, params.mode))
        .collect();
    let bounds = analyze_bounds(&log_pixels, w as usize, h as usize);
    // Report the EFFECTIVE points (after click override + tweak), so the readout
    // matches what the pipeline applies.
    let (bp, wp) = resolve_points(&bounds, &params);
    let (black_point, white_point) = points_to_display_255(&bp, &wp, params.mode);

    let processed = run_pipeline(&base_image_for_processing, &params, Some(bounds));

    let mut buf = Cursor::new(Vec::new());
    processed
        .to_rgb8()
        .write_with_encoder(JpegEncoder::new_with_quality(&mut buf, 80))
        .map_err(|e| e.to_string())?;

    let base64_str = general_purpose::STANDARD.encode(buf.get_ref());
    Ok(NegativePreviewResult {
        image: format!("data:image/jpeg;base64,{}", base64_str),
        black_point,
        white_point,
        center_margin: CENTER_MARGIN,
    })
}

#[tauri::command]
pub async fn convert_negatives(
    paths: Vec<String>,
    params: NegativeConversionParams,
    app_handle: AppHandle,
) -> Result<Vec<String>, String> {
    tokio::task::spawn_blocking(move || {
        let mut results = Vec::new();

        for (i, path_str) in paths.iter().enumerate() {
            let _ = app_handle.emit(
                "negative-batch-progress",
                serde_json::json!({
                    "current": i + 1,
                    "total": paths.len(),
                    "path": path_str
                }),
            );

            let (source_path, _) = parse_virtual_path(path_str);
            let real_path = source_path.to_string_lossy().to_string();

            let settings = load_settings(app_handle.clone()).unwrap_or_default();

            let img = match read_file_mapped(Path::new(&real_path)) {
                Ok(mmap) => load_base_image_from_bytes(&mmap, &real_path, false, &settings, None),
                Err(_) => {
                    let bytes = fs::read(&real_path).unwrap_or_default();
                    load_base_image_from_bytes(&bytes, &real_path, false, &settings, None)
                }
            }
            .map_err(|e| e.to_string())?;

            let bounds_ref = downscale_f32_image(&img, 1080, 1080);
            let ref_rgb = bounds_ref.to_rgb32f();
            let (ref_w, ref_h) = ref_rgb.dimensions();
            let log_pixels: Vec<f32> = ref_rgb
                .as_raw()
                .par_iter()
                .map(|&v| to_working(v, params.mode))
                .collect();
            let bounds = analyze_bounds(&log_pixels, ref_w as usize, ref_h as usize);

            let processed = run_pipeline(&img, &params, Some(bounds));

            let p = Path::new(&real_path);
            let parent = p.parent().unwrap_or(Path::new(""));
            let stem = p.file_stem().unwrap_or_default().to_string_lossy();
            let filename = format!("{}_Positive.tiff", stem);
            let out_path = parent.join(&filename);

            processed
                .to_rgb16()
                .save(&out_path)
                .map_err(|e| format!("Failed to save {}: {}", filename, e))?;

            let _ = crate::exif_processing::write_rrexif_sidecar(&real_path, &out_path);
            results.push(out_path.to_string_lossy().to_string());
        }

        Ok(results)
    })
    .await
    .map_err(|e| e.to_string())?
}

// Sample the clicked point on the cached downscaled negative and return its
// per-channel value in the CURRENT mode's working space, ready to drop into
// `bp_override` / `wp_override`. `x`/`y` are normalized [0,1] over the preview.
// Averages a small patch for stability (mirrors the main editor's WB picker).
#[tauri::command]
pub async fn sample_negative_point(
    path: String,
    x: f32,
    y: f32,
    mode: ConversionMode,
    state: tauri::State<'_, AppState>,
) -> Result<[f32; 3], String> {
    let (source_path, _) = parse_virtual_path(&path);
    let source_path_str = source_path.to_string_lossy().to_string();

    let mut hasher = DefaultHasher::new();
    source_path_str.hash(&mut hasher);
    "negative_preview_base".hash(&mut hasher);
    let cache_key = hasher.finish();

    let img = {
        let cache = state.geometry_cache.lock().unwrap();
        cache.get(&cache_key).cloned()
    }
    .ok_or_else(|| "Preview not ready for sampling".to_string())?;

    let rgb = img.to_rgb32f();
    let (w, h) = rgb.dimensions();
    if w == 0 || h == 0 {
        return Err("Empty image".to_string());
    }

    let cx = (x.clamp(0.0, 1.0) * (w as f32 - 1.0)).round() as i32;
    let cy = (y.clamp(0.0, 1.0) * (h as f32 - 1.0)).round() as i32;

    let radius: i32 = 2;
    let mut sum = [0.0f64; 3];
    let mut count = 0u32;
    for dy in -radius..=radius {
        for dx in -radius..=radius {
            let sx = cx + dx;
            let sy = cy + dy;
            if sx >= 0 && sy >= 0 && (sx as u32) < w && (sy as u32) < h {
                let p = rgb.get_pixel(sx as u32, sy as u32);
                sum[0] += p[0] as f64;
                sum[1] += p[1] as f64;
                sum[2] += p[2] as f64;
                count += 1;
            }
        }
    }
    if count == 0 {
        return Err("No pixels sampled".to_string());
    }

    let mut out = [0.0f32; 3];
    for c in 0..3 {
        let v = (sum[c] / count as f64) as f32;
        // working space matching the current mode (same transform run_pipeline uses)
        out[c] = to_working(v, mode);
    }
    Ok(out)
}
