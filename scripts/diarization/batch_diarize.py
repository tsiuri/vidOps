#!/usr/bin/env python3
"""
Batch diarization processing for multiple videos.
Processes a list of YTIDs with progress tracking and error handling.
"""
import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional
import yaml
import torch

# PyTorch 2.6+ compatibility: ensure pyannote checkpoints load with weights_only disabled
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Import our diarization modules
from diarize_inference import run_diarization
from match_reference import (
    load_diarization,
    extract_speaker_embeddings,
    load_reference_embeddings,
    match_speakers,
    apply_mapping,
)
from postprocess_and_map import (
    postprocess_diarization,
    map_words_to_speakers,
    find_words_file,
)
from parallel_vad_preprocess import parallel_vad_preprocess

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    print("Warning: tqdm not installed, progress bar disabled", file=sys.stderr)
    print("Install with: pip install tqdm", file=sys.stderr)


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load diarization configuration from YAML file."""
    if config_path is None:
        tool_root = Path(os.environ.get("TOOL_ROOT", Path(__file__).parents[2]))
        config_path = tool_root / "config" / "diarization.yaml"

    if not config_path.exists():
        print(f"Warning: Config file not found: {config_path}", file=sys.stderr)
        print("Using default settings", file=sys.stderr)
        return {"diarization": {}}

    with config_path.open() as f:
        return yaml.safe_load(f)


def load_ytids(ytids_file: Path) -> List[str]:
    """Load list of YTIDs from file (one per line or TSV)."""
    ytids = []

    with ytids_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Handle TSV (take first column)
            ytid = line.split("\t")[0]
            ytids.append(ytid)

    return ytids


def find_audio_file(
    ytid: str,
    project_root: Path,
    config: dict,
) -> Optional[Path]:
    """
    Locate audio file for YTID.

    Search order:
    1. Preprocessed audio: generated/diarization_inputs/<ytid>/enhanced.wav
    2. Fallback: pull/<ytid>__*.{mp4,webm,mkv,opus,etc}
    """
    diar_config = config.get("diarization", {})
    paths_config = diar_config.get("paths", {})

    # Try preprocessed audio
    input_audio_dir = project_root / paths_config.get("input_audio_dir", "generated/diarization_inputs")
    input_filename = paths_config.get("input_audio_filename", "enhanced.wav")
    preprocessed = input_audio_dir / ytid / input_filename

    if preprocessed.exists():
        return preprocessed

    # Try canonical.wav as alternative
    if input_filename != "canonical.wav":
        canonical = input_audio_dir / ytid / "canonical.wav"
        if canonical.exists():
            return canonical

    # Fallback: search pull directory
    fallback_dir = project_root / paths_config.get("fallback_audio_dir", "pull")
    if fallback_dir.exists():
        # Common video extensions
        exts = ["mp4", "webm", "mkv", "opus", "m4a", "mp3", "wav"]
        for ext in exts:
            # Try with full filename pattern
            matches = list(fallback_dir.glob(f"{ytid}__*.{ext}"))
            if matches:
                return matches[0]

            # Try without __date pattern
            matches = list(fallback_dir.glob(f"{ytid}.{ext}"))
            if matches:
                return matches[0]

    return None


def maybe_prompt_reference(
    reference_name: str,
    ref_base: Path,
    project_root: Path,
    config: dict,
    ytids: List[str],
) -> Optional[Path]:
    """
    If the reference directory is missing, interactively prompt to build
    a 50-clip reference using build_reference.py.
    """
    reference_dir = ref_base / reference_name
    if reference_dir.exists():
        return reference_dir

    # Skip prompts in non-interactive/worker mode
    if os.environ.get("BATCH_DIARIZE_SKIP_PROMPT") or not sys.stdin.isatty():
        return None

    print(f"[!] Reference not found: {reference_dir}")
    resp = input("Build reference now from available audio? [y/N]: ").strip().lower()
    if resp not in {"y", "yes"}:
        return None

    # Collect available audio sources from provided YTIDs
    sources_found = []
    for ytid in ytids:
        candidate = find_audio_file(ytid, project_root, config)
        if candidate and candidate.exists():
            sources_found.append(candidate)

    prompt = "Enter source audio path(s) comma-separated"
    if sources_found:
        prompt += f" [default: {len(sources_found)} collected from YTIDs]"
    prompt += ": "

    src_input = input(prompt).strip()
    if not src_input:
        if not sources_found:
            print("L Error: No source audio provided; cannot build reference.", file=sys.stderr)
            return None
        sources = sources_found
    else:
        sources = [Path(s.strip()) for s in src_input.split(",") if s.strip()]

    build_script = Path(__file__).parent / "build_reference.py"
    cmd = [sys.executable, str(build_script), reference_name] + [str(s) for s in sources]
    print(f"[*] Building reference '{reference_name}' using {len(sources)} source(s)...")
    try:
        subprocess.run(cmd, check=True, cwd=project_root)
    except subprocess.CalledProcessError as e:
        print(f"L Error: Failed to build reference: {e}", file=sys.stderr)
        return None

    if reference_dir.exists():
        print(f"[✓] Reference created at {reference_dir}")
        return reference_dir

    print("L Error: Reference build completed but directory not found.", file=sys.stderr)
    return None


def batch_diarize(
    ytids_file: Path,
    output_dir: Path,
    reference_name: Optional[str] = None,
    config_file: Optional[Path] = None,
    device: Optional[str] = None,
    continue_on_error: Optional[bool] = None,
    match_threshold: Optional[float] = None,
    match_margin: Optional[float] = None,
    match_force_best: Optional[bool] = None,
    verbose: bool = True,
) -> dict:
    """
    Batch process multiple videos through diarization pipeline.

    Args:
        ytids_file: File with list of YTIDs (one per line or TSV)
        output_dir: Base output directory
        reference_name: Optional reference name for speaker matching
        config_file: Optional config file (default: config/diarization.yaml)
        device: Device (auto|cuda|cpu)
        continue_on_error: Skip failed files vs abort
        match_threshold: Override similarity threshold for reference matching
        match_margin: Override margin between best and second-best matches
        match_force_best: Always map the top diarized speaker to the reference even
                          if below threshold/margin (default: True unless overridden)
        verbose: Print progress

    Returns:
        Summary statistics dict
    """
    # Load configuration
    config = load_config(config_file)
    diar_config = config.get("diarization", {})
    hyper = diar_config.get("hyperparameters", {})
    # Env overrides for per-GPU tuning
    try:
        seg_bs_env = os.environ.get("DIAR_SEGMENTATION_BATCH_SIZE")
        emb_bs_env = os.environ.get("DIAR_EMBEDDING_BATCH_SIZE")
        if seg_bs_env:
            hyper["segmentation_batch_size"] = int(seg_bs_env)
        if emb_bs_env:
            hyper["embedding_batch_size"] = int(emb_bs_env)
    except Exception:
        pass
    device = device if device is not None else diar_config.get("device", "auto")
    ref_config = diar_config.get("references", {})
    segmentation_cfg = diar_config.get("segmentation", {})
    segmentation_override = segmentation_cfg.get("override")
    min_speakers = hyper.get("min_speakers")
    max_speakers = hyper.get("max_speakers")
    min_cluster_size = hyper.get("min_cluster_size", 15)
    batch_cfg = diar_config.get("batch", {})
    if continue_on_error is None:
        continue_on_error = batch_cfg.get("continue_on_error", True)
    hf_cfg = diar_config.get("huggingface", {})
    effective_match_threshold = (
        match_threshold if match_threshold is not None else ref_config.get("match_threshold", 0.85)
    )
    effective_match_margin = (
        match_margin if match_margin is not None else ref_config.get("match_margin", 0.05)
    )
    effective_force_best = (
        match_force_best if match_force_best is not None else ref_config.get("force_best", True)
    )

    # Get project root
    project_root = Path(os.environ.get("PROJECT_ROOT", "."))

    # Load YTIDs
    ytids = load_ytids(ytids_file)

    if verbose:
        print(f"[*] Batch Diarization")
        print(f"   YTIDs file: {ytids_file}")
        print(f"   Total videos: {len(ytids)}")
        print(f"   Output: {output_dir}")
        if reference_name:
            print(f"   Reference: {reference_name} (threshold {effective_match_threshold}, margin {effective_match_margin}, force_best {effective_force_best})")
        print(f"   Device: {device}")
        print()

    # Preprocess audio (canonicalize + VAD + padding) before diarization
    paths_config = diar_config.get("paths", {})
    preprocess_cfg = diar_config.get("preprocess", {})
    pull_dir = project_root / paths_config.get("fallback_audio_dir", "pull")
    input_audio_dir = project_root / paths_config.get("input_audio_dir", "generated/diarization_inputs")
    preprocess_workers = preprocess_cfg.get("workers")
    preprocess_chunk = preprocess_cfg.get("chunk_duration", hyper.get("chunk_duration", 15.0))
    effective_workers = (
        preprocess_workers
        if preprocess_workers is not None
        else max(1, (os.cpu_count() or 2) - 2)
    )

    # Resolve python binary for the embedded VAD script; prefer venv, fall back to current python
    venv_env = os.environ.get("VIRTUAL_ENV")
    venv_python = (
        Path(venv_env) / "bin" / "python" if venv_env else project_root / ".venv" / "bin" / "python"
    )
    if not venv_python.exists():
        venv_python = Path(sys.executable)

    input_audio_dir.mkdir(parents=True, exist_ok=True)

    skip_pre = os.environ.get("BATCH_DIARIZE_SKIP_PREPROCESS", "").lower() in {"1", "true", "yes"}
    if pull_dir.exists() and not skip_pre:
        if verbose:
            print("[*] Running preprocessing (canonicalize + VAD + padding)...")
            print(f"    Preprocess workers: {effective_workers}")

        try:
            pre_stats = parallel_vad_preprocess(
                ytids_file=ytids_file,
                pull_dir=pull_dir,
                output_base=input_audio_dir,
                venv_python=venv_python,
                num_workers=preprocess_workers,
                chunk_duration=preprocess_chunk,
                verbose=True,  # Always verbose to see progress and debug hangs
            )

            if verbose:
                print(
                    f"[✓] Preprocess: total {pre_stats['total']} | processed {pre_stats['processed']} | skipped {pre_stats['skipped']} | failed {pre_stats['failed']}"
                )

            if pre_stats.get("failed", 0) > 0 and not continue_on_error:
                return {
                    "error": "Preprocessing failed for some items",
                    "failed": pre_stats.get("failed", 0),
                }
        except Exception as e:
            if not continue_on_error:
                return {"error": f"Preprocessing failed: {e}", "failed": len(ytids)}
            if verbose:
                print(f"    Warning: Preprocessing failed but continuing: {e}")
    elif not pull_dir.exists():
        if verbose:
            print(f"[!] Preprocessing skipped: pull directory not found at {pull_dir}")
    else:
        if verbose:
            print("[!] Preprocessing skipped by env BATCH_DIARIZE_SKIP_PREPROCESS")

    # Prepare reference if needed
    reference_dir = None
    if reference_name:
        ref_base = project_root / ref_config.get("directory", "data/references")
        if os.environ.get("BATCH_DIARIZE_SKIP_PROMPT"):
            reference_dir = ref_base / reference_name
            if not reference_dir.exists():
                return {"error": f"Reference not found: {reference_dir}"}
        else:
            reference_dir = maybe_prompt_reference(
                reference_name=reference_name,
                ref_base=ref_base,
                project_root=project_root,
                config=config,
                ytids=ytids,
            )
            if not reference_dir:
                return {"error": "Reference not found and not built"}

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load embedding model if matching
    embedding_model = None
    reference_embeddings = None

    if reference_name:
        if verbose:
            print("=� Loading embedding model for reference matching...")

        from pyannote.audio import Inference
        from pyannote.audio import Model

        # Use same token logic as for main pipeline
        hf_env_token = os.environ.get("HF_TOKEN") or os.environ.get("PYANNOTE_AUTH_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        hf_token = hf_env_token if hf_env_token else None

        model_obj = Model.from_pretrained(
            "pyannote/embedding",
            token=hf_token,
        )

        embedding_model = Inference(
            model_obj,
            window="whole",
            device=torch.device(device)
        )

        # Load reference embeddings once
        if verbose:
            print(f"=� Loading reference embeddings from {reference_name}...")

        reference_embeddings = load_reference_embeddings(
            reference_dir,
            embedding_model,
            reference_name=reference_name,
            verbose=False,
        )

        if verbose:
            print(f" Reference loaded ({len(reference_embeddings)} speakers)\n")

    # Process files
    stats = {
        "total": len(ytids),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "timings": [],
    }

    # Progress iterator
    iterator = tqdm(ytids, desc="Diarizing") if HAS_TQDM and verbose else ytids

    for ytid in iterator:
        try:
            # Find audio file
            audio_path = find_audio_file(ytid, project_root, config)

            if not audio_path:
                if verbose and not HAS_TQDM:
                    print(f"�  Skipped: {ytid} (audio not found)")
                stats["skipped"] += 1
                stats["errors"].append({"ytid": ytid, "error": "Audio not found"})
                continue

            # Run inference
            start_time = datetime.now()

            if verbose and not HAS_TQDM:
                print(f"<� Processing: {ytid}")

            use_auth_token = hf_cfg.get("use_auth_token", True)
            metadata = run_diarization(
                audio_path=audio_path,
                ytid=ytid,
                output_dir=output_dir,
                device=device,
                chunk_duration=hyper.get("chunk_duration", 15.0),
                overlap_duration=hyper.get("overlap_duration", 2.5),
                threshold=hyper.get("threshold", 0.6),
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                min_cluster_size=min_cluster_size,
                segmentation_batch_size=hyper.get("segmentation_batch_size", 32),
                embedding_batch_size=hyper.get("embedding_batch_size", 64),
                segmentation_model=segmentation_override,
                model=diar_config.get("model", "pyannote/speaker-diarization-3.1"),
                use_auth_token=use_auth_token,
                verbose=False,  # Suppress per-file output
            )

            elapsed = (datetime.now() - start_time).total_seconds()
            stats["timings"].append(elapsed)

            # Match to reference if provided
            if reference_name and embedding_model and reference_embeddings:
                diar_dir = output_dir / ytid
                tsv_path = diar_dir / "diarized_timestamps.tsv"

                # Load speakers
                speakers_data = load_diarization(tsv_path)

                # Extract embeddings
                speaker_embeddings = extract_speaker_embeddings(
                    audio_path=audio_path,
                    speakers_data=speakers_data,
                    embedding_model=embedding_model,
                    max_segments_per_speaker=ref_config.get("max_segments_per_speaker", 10),
                    verbose=False,
                )

                # Match
                mapping, mapping_details = match_speakers(
                    speaker_embeddings=speaker_embeddings,
                    reference_embeddings=reference_embeddings,
                    threshold=effective_match_threshold,
                    margin=effective_match_margin,
                    force_best=effective_force_best,
                    collect_details=True,
                    verbose=False,
                )

                # Apply mapping
                output_tsv = diar_dir / "diarized_timestamps_matched.tsv"
                apply_mapping(tsv_path, output_tsv, mapping, verbose=False)

                # Update metadata
                with (diar_dir / "diarization.json").open() as f:
                    metadata = json.load(f)

                metadata["reference"] = {
                    "reference_dir": str(reference_dir),
                    "reference_name": reference_name,
                    "threshold": effective_match_threshold,
                    "margin": effective_match_margin,
                    "force_best": effective_force_best,
                    "mapping": mapping,
                    "mapping_details": mapping_details,
                    "matched_output": str(output_tsv),
                }

                with (diar_dir / "diarization.json").open("w") as f:
                    json.dump(metadata, f, indent=2)

            # Post-processing: merge gaps and cleanup short segments
            postproc_cfg = diar_config.get("post_processing", {})
            if postproc_cfg.get("enabled", True):
                # Determine which timestamps file to post-process
                timestamps_to_process = diar_dir / "diarized_timestamps_matched.tsv" if reference_name else diar_dir / "diarized_timestamps.tsv"

                if timestamps_to_process.exists():
                    clean_timestamps = diar_dir / "diarized_timestamps_clean.tsv"

                    try:
                        postproc_stats = postprocess_diarization(
                            timestamps_file=timestamps_to_process,
                            output_file=clean_timestamps,
                            ytid=ytid,
                            micro_gap=postproc_cfg.get("micro_gap", 0.3),
                            min_turn=postproc_cfg.get("min_turn", 0.45),
                            source_audio="enhanced.wav",
                            verbose=verbose and not HAS_TQDM,
                        )

                        # Update metadata with post-processing stats
                        with (diar_dir / "diarization.json").open() as f:
                            metadata = json.load(f)
                        metadata["post_processing"] = postproc_stats
                        with (diar_dir / "diarization.json").open("w") as f:
                            json.dump(metadata, f, indent=2)

                    except Exception as e:
                        if verbose and not HAS_TQDM:
                            print(f"    Warning: Post-processing failed: {e}")

            # Word mapping: assign speakers to transcript words
            word_map_cfg = diar_config.get("word_mapping", {})
            if word_map_cfg.get("enabled", True):
                # Find words file
                words_file = find_words_file(ytid, project_root)

                if words_file and words_file.exists():
                    # Use cleaned timestamps if available, otherwise use matched/raw
                    clean_timestamps = diar_dir / "diarized_timestamps_clean.tsv"
                    timestamps_for_mapping = clean_timestamps if clean_timestamps.exists() else (
                        diar_dir / "diarized_timestamps_matched.tsv" if reference_name and (diar_dir / "diarized_timestamps_matched.tsv").exists()
                        else diar_dir / "diarized_timestamps.tsv"
                    )

                    if timestamps_for_mapping.exists():
                        speaker_words = diar_dir / "speaker_words.tsv"

                        try:
                            word_map_stats = map_words_to_speakers(
                                words_file=words_file,
                                timestamps_file=timestamps_for_mapping,
                                output_file=speaker_words,
                                gap_tolerance=word_map_cfg.get("gap_tolerance", 0.15),
                                verbose=verbose and not HAS_TQDM,
                            )

                            # Update metadata with word mapping stats
                            with (diar_dir / "diarization.json").open() as f:
                                metadata = json.load(f)
                            metadata["word_mapping"] = word_map_stats
                            with (diar_dir / "diarization.json").open("w") as f:
                                json.dump(metadata, f, indent=2)

                        except Exception as e:
                            if verbose and not HAS_TQDM:
                                print(f"    Warning: Word mapping failed: {e}")
                elif verbose and not HAS_TQDM:
                    print(f"    Warning: Words file not found for {ytid}")

            stats["processed"] += 1

            if verbose and not HAS_TQDM:
                print(f"    Completed in {elapsed:.1f}s")

        except Exception as e:
            stats["failed"] += 1
            stats["errors"].append({"ytid": ytid, "error": str(e)})

            if verbose and not HAS_TQDM:
                print(f"L Failed: {ytid} - {e}")

            if not continue_on_error:
                raise
        finally:
            # Free cached GPU memory between files to avoid accumulation on long jobs
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # Print summary
    if verbose:
        print(f"\n=� Batch Summary:")
        print(f"   Total: {stats['total']}")
        print(f"   Processed: {stats['processed']}")
        print(f"   Skipped: {stats['skipped']}")
        print(f"   Failed: {stats['failed']}")

        if stats["timings"]:
            import statistics
            avg_time = statistics.mean(stats["timings"])
            med_time = statistics.median(stats["timings"])
            print(f"\n�  Timing:")
            print(f"   Average: {avg_time:.1f}s per file")
            print(f"   Median: {med_time:.1f}s per file")

        if stats["errors"] and verbose:
            print(f"\nL Errors ({len(stats['errors'])}):")
            for err in stats["errors"][:10]:  # Show first 10
                print(f"   {err['ytid']}: {err['error']}")
            if len(stats["errors"]) > 10:
                print(f"   ... and {len(stats['errors']) - 10} more")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Batch diarization for multiple videos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic batch processing
  %(prog)s ytids.txt results/diarization

  # With reference matching
  %(prog)s ytids.txt results/diarization --reference speaker_alpha

  # With custom config
  %(prog)s ytids.txt results/diarization --config config/custom.yaml

  # Abort on first error
  %(prog)s ytids.txt results/diarization --abort-on-error
        """
    )

    parser.add_argument(
        "ytids_file",
        type=Path,
        help="File with YTIDs (one per line or TSV)"
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory"
    )
    parser.add_argument(
        "--reference",
        dest="reference_name",
        help="Reference name for speaker matching"
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        dest="match_threshold",
        help="Similarity threshold for reference matching (default: config references.match_threshold)"
    )
    parser.add_argument(
        "--match-margin",
        type=float,
        dest="match_margin",
        help="Margin between best and second-best matches (default: config references.match_margin)"
    )
    parser.add_argument(
        "--match-force-best",
        action="store_true",
        dest="match_force_best",
        help="Always map the top diarized speaker to the reference even if below threshold/margin (default: enabled)"
    )
    parser.add_argument(
        "--no-match-force-best",
        action="store_false",
        dest="match_force_best",
        help="Disable forced mapping; require threshold/margin to map"
    )
    parser.set_defaults(match_force_best=None)
    parser.add_argument(
        "--config",
        type=Path,
        dest="config_file",
        help="Config file (default: config/diarization.yaml)"
    )
    parser.add_argument(
        "--device",
        dest="device",
        default=None,
        help="Device: auto, cuda, cpu (default: config/diarization.yaml or auto)"
    )
    parser.add_argument(
        "--continue-on-error",
        dest="continue_on_error",
        action="store_true",
        help="Continue after failures (default: config setting)"
    )
    parser.add_argument(
        "--abort-on-error",
        dest="continue_on_error",
        action="store_false",
        help="Abort on first error (overrides config)"
    )
    parser.set_defaults(continue_on_error=None)
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.ytids_file.exists():
        print(f"L Error: YTIDs file not found: {args.ytids_file}", file=sys.stderr)
        sys.exit(1)

    # Run batch processing
    try:
        stats = batch_diarize(
            ytids_file=args.ytids_file,
            output_dir=args.output_dir,
            reference_name=args.reference_name,
            config_file=args.config_file,
            device=args.device,
            continue_on_error=args.continue_on_error,
            match_threshold=args.match_threshold,
            match_margin=args.match_margin,
            match_force_best=args.match_force_best,
            verbose=not args.quiet,
        )

        # Exit code based on results
        if stats.get("error"):
            sys.exit(1)
        elif stats["failed"] > 0:
            sys.exit(2 if (args.continue_on_error is not False) else 0)
        else:
            sys.exit(0)

    except Exception as e:
        print(f"L Fatal error: {e}", file=sys.stderr)
        if not args.quiet:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
