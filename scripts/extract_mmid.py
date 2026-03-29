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
    count_be = int.from_bytes(data[6:8], "big")
    count_le = int.from_bytes(data[6:8], "little")
    max_entries = (len(data) - 16) // 4
    if 0 < count_be <= max_entries:
        return count_be
    if 0 < count_le <= max_entries:
        return count_le
    raise ParseError("Could not determine chunk count")


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
    table_off = 16
    offsets = [int.from_bytes(blob[table_off + 4 * i:table_off + 4 * i + 4], "little") for i in range(count)]
    offsets = sorted(set([o for o in offsets if 0 <= o < len(blob)]))
    for i, off in enumerate(offsets):
        next_off = offsets[i + 1] if i + 1 < len(offsets) else len(blob)
        if next_off <= off:
            continue
        chunk = blob[off:next_off]
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
        blob = data[off:next_off]
        if len(blob) < 20:
            continue
        try:
            _parse_chunk_count(blob)
        except Exception:
            continue

        # Write full sequence blob as .mmd for downstream workflows.
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

