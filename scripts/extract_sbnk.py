import argparse
from pathlib import Path


SCAN_EXTS = {
    ".bin", ".data", ".dat", ".pak", ".arc", ".img", ".fog", ".mmd", ".str", ".exe", ""
}


def is_candidate_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in SCAN_EXTS:
        return True
    name_lower = path.name.lower()
    return name_lower.endswith(".data.bin")


def u16_le(data, off):
    if off + 2 > len(data):
        return 0
    return int.from_bytes(data[off:off + 2], "little")


def u32_le(data, off):
    if off + 4 > len(data):
        return 0
    return int.from_bytes(data[off:off + 4], "little")


def parse_vh_layers(vh: bytes):
    if len(vh) < 0x40:
        return None
    max_patches = vh[0x1A]
    patch_meta_off = u32_le(vh, 0x24)
    layer_table_off = u32_le(vh, 0x28)
    vb_size = u32_le(vh, 0x30)

    if max_patches == 0 or max_patches > 64:
        return None
    if patch_meta_off <= 0 or patch_meta_off >= len(vh):
        return None
    if layer_table_off <= 0 or layer_table_off >= len(vh):
        return None
    if vb_size < 0x1000 or vb_size > 0x2000000:
        return None
    if patch_meta_off + (max_patches * 8) > len(vh):
        return None

    offsets = []
    current_layer_idx = 0
    processed = 0
    meta_off = patch_meta_off
    good_layer_rows = 0
    total_layer_rows = 0
    max_layer_end = 0

    while meta_off + 8 <= len(vh) and processed < max_patches:
        entry = vh[meta_off:meta_off + 8]
        layer_count = entry[0]

        if all(b == 0 for b in entry):
            meta_off += 8
            processed += 1
            continue

        patch_vol = entry[1]
        if patch_vol > 127:
            return None

        if layer_count == 0 or layer_count > 32:
            meta_off += 8
            processed += 1
            continue

        layer_base_off = u32_le(entry, 4)
        for layer_num in range(1, layer_count + 1):
            if layer_base_off:
                layer_off = layer_base_off + ((layer_num - 1) * 24)
            else:
                layer_off = layer_table_off + (current_layer_idx * 24)
            if layer_off + 24 > len(vh):
                break
            ld = vh[layer_off:layer_off + 24]
            total_layer_rows += 1

            min_key = ld[6]
            max_key = ld[7]
            root = ld[2]
            if min_key <= max_key <= 127 and root <= 127:
                good_layer_rows += 1

            vb_offset = u32_le(ld, 0x14)
            if vb_offset < vb_size:
                offsets.append(vb_offset)
            max_layer_end = max(max_layer_end, layer_off + 24)
            current_layer_idx += 1

        meta_off += 8
        processed += 1

    if not offsets or total_layer_rows == 0:
        return None

    quality = good_layer_rows / float(total_layer_rows)
    if quality < 0.75:
        return None

    offsets = sorted(set(offsets))
    vh_size_est = max(patch_meta_off + max_patches * 8, layer_table_off + current_layer_idx * 24, max_layer_end)
    if vh_size_est <= 0 or vh_size_est > 0x10000:
        return None

    return {
        "max_patches": max_patches,
        "vb_size": vb_size,
        "offsets": offsets,
        "vh_size_est": vh_size_est,
    }


def score_vb_base(blob: bytes, base: int, vb_size: int, sample_offsets):
    checks = 0
    score = 0
    for off in sample_offsets:
        if off < 0 or off + 2 > vb_size:
            continue
        pos = base + off
        if pos + 2 > len(blob):
            continue
        checks += 1
        b0 = blob[pos]
        b1 = blob[pos + 1]
        ok_header = b0 <= 0x4F
        ok_flags = b1 <= 0x07
        if ok_header and ok_flags:
            score += 1.0
        elif ok_flags:
            score += 0.4
    if checks == 0:
        return 0.0
    return score / checks


def _leading_zeros_upto16(blob: bytes, base: int):
    run = 0
    lim = min(len(blob), base + 16)
    for i in range(base, lim):
        if blob[i] != 0:
            break
        run += 1
    return run


def _sample_end_flag_score(blob: bytes, base: int, vb_size: int, sample_offsets):
    if not sample_offsets:
        return 0.0
    ok = 0
    total = 0
    for i, start in enumerate(sample_offsets):
        end = sample_offsets[i + 1] if i + 1 < len(sample_offsets) else vb_size
        if end <= start or end - start < 16:
            continue
        flag_pos = base + end - 15
        if flag_pos < 0 or flag_pos >= len(blob):
            continue
        total += 1
        if blob[flag_pos] & 0x01:
            ok += 1
    if total == 0:
        return 0.0
    return ok / float(total)


def find_vb_base(blob: bytes, vb_size: int, offsets):
    end = len(blob) - vb_size
    if end < 0:
        return None

    if len(offsets) > 48:
        step = max(1, len(offsets) // 48)
        sample_offsets = offsets[::step][:48]
    else:
        sample_offsets = offsets

    best_base = None
    best_score = -1.0
    best_end_score = -1.0
    best_zero_run = -1

    for base in range(0, end + 1, 4):
        s = score_vb_base(blob, base, vb_size, sample_offsets)
        if s < 0.70:
            continue
        e = _sample_end_flag_score(blob, base, vb_size, sample_offsets)
        z = _leading_zeros_upto16(blob, base)

        better = False
        if s > best_score + 1e-12:
            better = True
        elif abs(s - best_score) < 1e-12:
            if e > best_end_score + 1e-12:
                better = True
            elif abs(e - best_end_score) < 1e-12 and z > best_zero_run:
                better = True

        if better:
            best_score = s
            best_end_score = e
            best_zero_run = z
            best_base = base

    if best_base is not None:
        return best_base, best_score
    return None


def find_vh_candidates(blob: bytes):
    candidates = []
    for off in range(0, len(blob) - 0x40, 4):
        max_patches = blob[off + 0x1A]
        if max_patches == 0 or max_patches > 64:
            continue
        patch_meta_off = u32_le(blob, off + 0x24)
        layer_table_off = u32_le(blob, off + 0x28)
        vb_size = u32_le(blob, off + 0x30)

        if not (0x20 <= patch_meta_off < 0x2000):
            continue
        if not (0x20 <= layer_table_off < 0x4000):
            continue
        if not (0x400 <= vb_size <= len(blob)):
            continue

        wnd_end = min(len(blob), off + 0x10000)
        vh_window = blob[off:wnd_end]
        parsed = parse_vh_layers(vh_window)
        if not parsed:
            continue

        if candidates and abs(off - candidates[-1]["offset"]) < 0x40:
            continue

        candidates.append(
            {
                "offset": off,
                "vb_size": parsed["vb_size"],
                "offsets": parsed["offsets"],
                "vh_size_est": parsed["vh_size_est"],
            }
        )
    return candidates


def choose_candidates(candidates, all_candidates=False):
    if all_candidates:
        return candidates
    if not candidates:
        return []
    best = max(candidates, key=lambda c: int(c.get("vb_size", 0)))
    return [best]


def extract_file(src: Path, output_root: Path, all_candidates: bool = False):
    blob = src.read_bytes()
    candidates = find_vh_candidates(blob)
    if not candidates:
        return 0

    resolved = []
    for cand in candidates:
        vb_hit = find_vb_base(blob, cand["vb_size"], cand["offsets"])
        if not vb_hit:
            continue
        vb_base, vb_score = vb_hit
        item = dict(cand)
        item["vb_base"] = vb_base
        item["vb_score"] = vb_score
        resolved.append(item)

    picked = choose_candidates(resolved, all_candidates=all_candidates)
    if not picked:
        return 0

    out_dir = output_root / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    extracted = 0
    for i, cand in enumerate(picked, 1):
        vh_off = cand["offset"]
        vb_size = cand["vb_size"]
        vb_base = cand["vb_base"]
        vh_end = min(len(blob), vh_off + int(cand.get("vh_size_est", 0x4000)) + 0x20)

        vh_data = blob[vh_off:vh_end]
        vb_data = blob[vb_base:vb_base + vb_size]

        stem = src.stem if len(picked) == 1 else f"{src.stem}_{i:02d}"
        (out_dir / f"{stem}.vh").write_bytes(vh_data)
        (out_dir / f"{stem}.vb").write_bytes(vb_data)
        extracted += 1
        print(f"[OK] {src.name}: extracted {stem} VH@0x{vh_off:X} VB@0x{vb_base:X} score={cand['vb_score']:.3f}")

    if len(resolved) > len(picked) and not all_candidates:
        print(f"[INFO] {src.name}: skipped {len(resolved) - len(picked)} smaller candidate(s)")

    return extracted


def extract_folder(input_root: Path, output_root: Path, all_candidates: bool = False, continue_on_error: bool = False):
    files = sorted([p for p in input_root.iterdir() if p.is_file() and is_candidate_file(p)], key=lambda p: p.name.lower())
    if not files:
        print(f"No candidate files found in {input_root}")
        return 0, 0, []

    ok_files = 0
    ok_banks = 0
    failed = []
    for src in files:
        try:
            count = extract_file(src, output_root, all_candidates=all_candidates)
            if count > 0:
                ok_files += 1
                ok_banks += count
            else:
                print(f"[SKIP] {src.name}: no valid SBNK candidate found")
        except Exception as exc:
            failed.append((src.name, str(exc)))
            print(f"[FAIL] {src.name}: {exc}")
            if not continue_on_error:
                break

    return ok_files, ok_banks, failed


def main():
    parser = argparse.ArgumentParser(description="Scan files for 989 Studios SBNK headers and extract .vh/.vb pairs")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--all-candidates", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    in_root = Path(args.input_root)
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ok_files, ok_banks, failed = extract_folder(
        in_root,
        out_root,
        all_candidates=args.all_candidates,
        continue_on_error=args.continue_on_error,
    )
    print(f"Done. SourceSuccess={ok_files} BanksExtracted={ok_banks} Failed={len(failed)}")


if __name__ == "__main__":
    main()

