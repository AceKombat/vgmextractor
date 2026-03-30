import argparse
from pathlib import Path


SCAN_EXTS = {
    ".bin", ".data", ".dat", ".pak", ".arc", ".img", ".fog", ".mmd", ".str", ".exe", ""
}


class ParseError(Exception):
    pass


def is_candidate_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in SCAN_EXTS:
        return True
    name_lower = path.name.lower()
    return name_lower.endswith(".data.bin")


def _parse_chunk_count(data: bytes) -> int:
    if len(data) < 20 or data[:4] != b"MMID":
        raise ParseError("Not MMID")

    # Primary rule observed in these banks: count is byte at 0x07.
    count_u8 = data[7]
    max_entries = (len(data) - 16) // 4
    if 0 < count_u8 <= max_entries:
        return count_u8

    # Fallback for variant headers.
    count_be = int.from_bytes(data[6:8], "big")
    count_le = int.from_bytes(data[6:8], "little")
    if 0 < count_be <= max_entries:
        return count_be
    if 0 < count_le <= max_entries:
        return count_le

    raise ParseError("Could not determine chunk count")


def _parse_chunk_offsets(blob: bytes, count: int) -> list[int]:
    table_off = 16
    offsets = []
    for i in range(count):
        pos = table_off + (4 * i)
        if pos + 4 > len(blob):
            break
        off = int.from_bytes(blob[pos:pos + 4], "little")
        if 0 <= off < len(blob):
            offsets.append(off)

    offsets = sorted(set(offsets))
    if not offsets:
        raise ParseError("No valid MMID chunk offsets")
    return offsets


def _find_cd_terminator(data: bytes, start: int, limit: int) -> int | None:
    idx = data.find(b"\xCD\xCD\xCD\xCD", start, limit)
    return idx if idx >= 0 else None


def _find_last_midi_eot_end(data: bytes, start: int, limit: int) -> int | None:
    # MIDI EOT is usually FF 2F 00, but some streams may present just FF 2F.
    # Keep trailing 0x00 padding bytes up to the next non-zero byte.
    sig = b"\xFF\x2F"
    pos = start
    last_end = -1
    while True:
        hit = data.find(sig, pos, limit)
        if hit < 0:
            break

        end = hit + 2
        if end < limit and data[end] == 0x00:
            # Consume length byte and any extra 32-bit alignment zeros.
            end += 1
            while end < limit and data[end] == 0x00:
                end += 1

        last_end = end
        pos = hit + 1

    return last_end if last_end >= 0 else None


def _last_chunk_end(blob: bytes, last_chunk_start: int, hard_limit: int) -> int:
    cd_term = _find_cd_terminator(blob, last_chunk_start, hard_limit)
    search_limit = cd_term if cd_term is not None else hard_limit

    eot_end = _find_last_midi_eot_end(blob, last_chunk_start, search_limit)
    if eot_end is not None:
        return eot_end

    if cd_term is not None:
        # No explicit MIDI end event found; cut at CD padding start.
        return cd_term

    # Last resort.
    return hard_limit


def _compute_blob_end(blob: bytes, offsets: list[int], hard_limit: int) -> int:
    last_end = 0
    for i, start in enumerate(offsets):
        if i + 1 < len(offsets):
            end = offsets[i + 1]
        else:
            end = _last_chunk_end(blob, start, hard_limit)
        if end > start:
            last_end = max(last_end, end)

    if last_end <= 0:
        return hard_limit
    return min(last_end, hard_limit)


def valid_mmid_offsets(data: bytes) -> list[int]:
    offsets = []
    start = 0
    while True:
        off = data.find(b"MMID", start)
        if off < 0:
            break
        try:
            _parse_chunk_count(data[off:])
            offsets.append(off)
        except Exception:
            pass
        start = off + 4
    return offsets


def iter_mmid_chunks(blob: bytes):
    count = _parse_chunk_count(blob)
    offsets = _parse_chunk_offsets(blob, count)

    for i, off in enumerate(offsets):
        if i + 1 < len(offsets):
            end = offsets[i + 1]
        else:
            end = _last_chunk_end(blob, off, len(blob))
        if end <= off:
            continue
        chunk = blob[off:end]
        if len(chunk) < 32 or chunk[:4] != b"MID ":
            continue
        yield i, off, chunk


def extract_file(src: Path, output_root: Path, dump_chunks: bool = False):
    data = src.read_bytes()
    mmid_offsets = valid_mmid_offsets(data)
    if not mmid_offsets:
        return 0, 0

    out_dir = output_root / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    blobs_written = 0
    chunks_written = 0

    for i, off in enumerate(mmid_offsets):
        next_off = mmid_offsets[i + 1] if i + 1 < len(mmid_offsets) else len(data)
        blob_candidate = data[off:next_off]
        if len(blob_candidate) < 20:
            continue

        try:
            count = _parse_chunk_count(blob_candidate)
            offsets = _parse_chunk_offsets(blob_candidate, count)
            blob_end = _compute_blob_end(blob_candidate, offsets, len(blob_candidate))
            blob = blob_candidate[:blob_end]
        except Exception:
            continue

        if len(blob) < 20:
            continue

        blob_path = out_dir / f"{src.stem}_blob{i:02d}.mmd"
        blob_path.write_bytes(blob)
        blobs_written += 1

        if dump_chunks:
            for chunk_idx, _chunk_off, chunk in iter_mmid_chunks(blob):
                chunk_path = out_dir / f"{src.stem}_blob{i:02d}_chunk{chunk_idx:02d}.bin"
                chunk_path.write_bytes(chunk)
                chunks_written += 1

    if blobs_written > 0:
        print(f"[OK] {src.name}: MMD blobs={blobs_written} chunks={chunks_written}")
    return blobs_written, chunks_written


def extract_folder(input_root: Path, output_root: Path, dump_chunks: bool = False, continue_on_error: bool = False):
    files = sorted([p for p in input_root.iterdir() if p.is_file() and is_candidate_file(p)], key=lambda p: p.name.lower())
    if not files:
        print(f"No candidate files found in {input_root}")
        return 0, 0, 0, []

    ok_files = 0
    ok_blobs = 0
    ok_chunks = 0
    failed = []

    for src in files:
        try:
            blobs, chunks = extract_file(src, output_root, dump_chunks=dump_chunks)
            if blobs > 0:
                ok_files += 1
                ok_blobs += blobs
                ok_chunks += chunks
            else:
                print(f"[SKIP] {src.name}: no MMID found")
        except Exception as exc:
            failed.append((src.name, str(exc)))
            print(f"[FAIL] {src.name}: {exc}")
            if not continue_on_error:
                break

    return ok_files, ok_blobs, ok_chunks, failed


def main():
    parser = argparse.ArgumentParser(description="Scan files for 989 MMID blocks and extract .mmd files")
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--dump-chunks", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    in_root = Path(args.input_root)
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    ok_files, ok_blobs, ok_chunks, failed = extract_folder(
        in_root,
        out_root,
        dump_chunks=args.dump_chunks,
        continue_on_error=args.continue_on_error,
    )
    print(f"Done. SourceSuccess={ok_files} BlobsExtracted={ok_blobs} ChunksExtracted={ok_chunks} Failed={len(failed)}")


if __name__ == "__main__":
    main()

