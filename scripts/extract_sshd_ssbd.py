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


def _u32_le(data: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(data):
        return 0
    return int.from_bytes(data[off:off + 4], "little")


def find_all(data: bytes, marker: bytes):
    start = 0
    while True:
        idx = data.find(marker, start)
        if idx < 0:
            return
        yield idx
        start = idx + 1


def _is_all_zero(buf: bytes) -> bool:
    return bool(buf) and all(b == 0 for b in buf)


def score_bd_candidate(seg: bytes) -> float:
    if len(seg) < 64:
        return 0.0

    # Required by observed BD layout: first row must be 16 zero bytes.
    if seg[:16] != b"\x00" * 16:
        return 0.0

    # Reject pre-padding starts: bytes from 0x10 onward should begin ADPCM payload.
    if seg[16] == 0:
        return 0.0

    # Tail should not be an all-zero 16-byte row.
    if _is_all_zero(seg[-16:]):
        return 0.0

    # PSX ADPCM block plausibility in early region.
    probe = min(len(seg), 16 * 1024)
    ok = 0
    total = 0
    flag_bias = 0
    for i in range(16, probe - 1, 16):
        b0 = seg[i]
        b1 = seg[i + 1]
        total += 1
        if b0 <= 0x4F and b1 <= 0x07:
            ok += 1
        if b1 in (0x00, 0x02):
            flag_bias += 1

    if total == 0:
        return 0.0

    adpcm_ratio = ok / float(total)
    bias_ratio = flag_bias / float(total)
    return (adpcm_ratio * 0.90) + (bias_ratio * 0.10)


def find_best_bd_offset(data: bytes, hd_end: int, total_bd_size: int, bd_markers: list[int], used_bd: set[int]):
    max_off = len(data) - total_bd_size
    if max_off < 0:
        return None, "none", 0.0

    candidates = []

    for cand in bd_markers:
        if cand in used_bd:
            continue
        if hd_end <= cand <= max_off:
            candidates.append((cand, "marker"))

    # Heuristic scan (4-byte stride).
    scan_start = max(hd_end, 0)
    for off in range(scan_start, max_off + 1, 4):
        if data[off:off + 16] == b"\x00" * 16:
            candidates.append((off, "heuristic"))

    best_off = None
    best_mode = "none"
    best_score = -1.0
    seen = set()
    for off, mode in candidates:
        if off in seen:
            continue
        seen.add(off)
        seg = data[off:off + total_bd_size]
        score = score_bd_candidate(seg)
        if mode == "marker":
            score += 0.01
        if score > best_score:
            best_score = score
            best_off = off
            best_mode = mode

    if best_off is None or best_score <= 0.0:
        return None, "none", 0.0
    return best_off, best_mode, best_score


def extract_from_blob(src: Path, data: bytes, out_dir: Path):
    hd_markers = list(find_all(data, b"SShd"))
    bd_markers = list(find_all(data, b"SSbd"))
    if not hd_markers:
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    used_bd = set()

    for n, hd_sig_off in enumerate(hd_markers):
        hd_off = hd_sig_off - 0x0C if hd_sig_off >= 0x0C else hd_sig_off
        total_hd_size = _u32_le(data, hd_off)
        total_bd_size = _u32_le(data, hd_off + 4)

        if total_hd_size <= 0 or total_hd_size > len(data) - hd_off:
            continue
        if total_bd_size <= 0 or total_bd_size > len(data):
            continue

        hd_end = hd_off + total_hd_size
        bd_off, source_mode, score = find_best_bd_offset(data, hd_end, total_bd_size, bd_markers, used_bd)
        if bd_off is None:
            continue

        hd_bytes = data[hd_off:hd_end]
        bd_bytes = data[bd_off:bd_off + total_bd_size]
        if len(hd_bytes) < 0x10 or hd_bytes[0x0C:0x10] != b"SShd":
            continue

        stem = f"{src.stem}_bank{n:02d}"
        hd_path = out_dir / f"{stem}.hd"
        bd_path = out_dir / f"{stem}.bd"
        hd_path.write_bytes(hd_bytes)
        bd_path.write_bytes(bd_bytes)
        used_bd.add(bd_off)
        extracted += 1
        print(
            f"[OK] {src.name}: extracted {stem} "
            f"(HD@0x{hd_off:X}, BD@0x{bd_off:X}, mode={source_mode}, score={score:.4f})"
        )

    return extracted


def extract_folder(input_root: Path, output_root: Path, continue_on_error: bool = False):
    files = sorted([p for p in input_root.iterdir() if p.is_file() and is_candidate_file(p)], key=lambda p: p.name.lower())
    if not files:
        print(f"No candidate files found in {input_root}")
        return 0, 0, []

    ok_files = 0
    ok_banks = 0
    failed = []
    for src in files:
        try:
            data = src.read_bytes()
            out_dir = output_root / src.stem
            count = extract_from_blob(src, data, out_dir)
            if count > 0:
                ok_files += 1
                ok_banks += count
            else:
                print(f"[SKIP] {src.name}: no valid SShd/SSbd pair found")
        except Exception as exc:
            failed.append((src.name, str(exc)))
            print(f"[FAIL] {src.name}: {exc}")
            if not continue_on_error:
                break

    return ok_files, ok_banks, failed


def main():
    parser = argparse.ArgumentParser(description="Scan files for Sony SShd/SSbd soundbanks and extract .hd/.bd pairs")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    in_root = Path(args.input_root)
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ok_files, ok_banks, failed = extract_folder(in_root, out_root, continue_on_error=args.continue_on_error)
    print(f"Done. SourceSuccess={ok_files} BanksExtracted={ok_banks} Failed={len(failed)}")


if __name__ == "__main__":
    main()

