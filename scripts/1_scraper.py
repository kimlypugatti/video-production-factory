#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yt_dlp


ROOT_DIR = Path(__file__).resolve().parents[1]

CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"

PLATFORMS_FILE = CONFIG_DIR / "platforms.json"
NICHES_FILE = CONFIG_DIR / "niches.json"
HISTORY_FILE = DATA_DIR / "processed_history.json"

VIDEOS_PER_CHANNEL = 20
MIN_VIDEO_DURATION = 30
DEFAULT_MIN_MULTIPLIER = 3.5
MAX_VIDEO_HEIGHT = 1080

NICHE_ORDER = [
    "irl_streaming",
    "strategy_and_skill_challenges",
    "business_podcasts",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def emit(
    message: str,
    log_callback: Optional[Callable[[str], None]] = None,
) -> None:
    formatted = f"[{timestamp()}] {message}"
    logging.info(message)

    if log_callback is not None:
        log_callback(formatted)


def read_json(path: Path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(temporary_path, path)


def validate_component(value: str, label: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError(f"Invalid {label}: {value}")

    return value


def load_platforms() -> list:
    platforms = read_json(PLATFORMS_FILE, [])

    if not isinstance(platforms, list):
        raise ValueError(
            "config/platforms.json must contain a JSON array."
        )

    return platforms


def load_platform_config(platform_name: str) -> dict:
    validate_component(platform_name, "platform name")

    for platform in load_platforms():
        if platform.get("platform") == platform_name:
            return platform

    raise ValueError(
        f"Platform not found in config/platforms.json: {platform_name}"
    )


def load_niches() -> dict:
    niches = read_json(NICHES_FILE, {})

    if not isinstance(niches, dict):
        raise ValueError(
            "config/niches.json must contain a JSON object."
        )

    return niches


def load_niche_config(niche_key: str) -> dict:
    if niche_key not in NICHE_ORDER:
        raise ValueError(f"Unsupported niche: {niche_key}")

    niches = load_niches()

    if niche_key not in niches:
        raise ValueError(
            f"Missing niche configuration: {niche_key}"
        )

    config = niches[niche_key]

    if not isinstance(config.get("target_channels"), list):
        raise ValueError(
            f"target_channels must be a list for {niche_key}."
        )

    if not config["target_channels"]:
        raise ValueError(
            f"No target channels configured for {niche_key}."
        )

    return config


def load_processed_history() -> set:
    history = read_json(HISTORY_FILE, [])

    if not isinstance(history, list):
        raise ValueError(
            "data/processed_history.json must contain a JSON array."
        )

    return {
        str(item.get("video_id"))
        if isinstance(item, dict)
        else str(item)
        for item in history
    }


def save_processed_video(video_id: str) -> None:
    history = read_json(HISTORY_FILE, [])

    if not isinstance(history, list):
        raise ValueError(
            "data/processed_history.json must contain a JSON array."
        )

    existing_ids = {
        str(item.get("video_id"))
        if isinstance(item, dict)
        else str(item)
        for item in history
    }

    if video_id not in existing_ids:
        history.append(video_id)

    write_json(HISTORY_FILE, history)


def normalize_channel_url(channel_url: str) -> str:
    channel_url = channel_url.strip().rstrip("/")

    if not channel_url.startswith("https://www.youtube.com/"):
        raise ValueError(
            f"Invalid YouTube channel URL: {channel_url}"
        )

    if not re.search(r"/(videos|streams|shorts)$", channel_url):
        channel_url = f"{channel_url}/videos"

    return channel_url


def normalize_video_url(entry: dict) -> Optional[str]:
    video_id = entry.get("id")

    if not video_id:
        return None

    entry_url = entry.get("url")

    if (
        isinstance(entry_url, str)
        and entry_url.startswith("https://")
    ):
        return entry_url

    return f"https://www.youtube.com/watch?v={video_id}"


def safe_number(value) -> Optional[float]:
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result < 0:
        return None

    return result


def extract_channel_entries(
    channel_url: str,
    log_callback=None,
) -> list:
    channel_url = normalize_channel_url(channel_url)

    options = {
        "extract_flat": True,
        "playlistend": VIDEOS_PER_CHANNEL,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "noplaylist": False,
        "extractor_retries": 2,
        "socket_timeout": 20,
    }

    emit(
        f"Scanning channel: {channel_url}",
        log_callback,
    )

    with yt_dlp.YoutubeDL(options) as downloader:
        result = downloader.extract_info(
            channel_url,
            download=False,
        )

    if not result:
        return []

    entries = result.get("entries") or []

    return [
        entry
        for entry in entries
        if entry and entry.get("id")
    ][:VIDEOS_PER_CHANNEL]


def extract_complete_video_metadata(
    entry: dict,
    downloader: yt_dlp.YoutubeDL,
) -> Optional[dict]:
    video_url = normalize_video_url(entry)

    if not video_url:
        return None

    video_id = str(entry["id"])

    info = downloader.extract_info(
        video_url,
        download=False,
    )

    if not info:
        return None

    view_count = safe_number(
        info.get("view_count", entry.get("view_count"))
    )

    duration = safe_number(
        info.get("duration", entry.get("duration"))
    )

    return {
        "id": video_id,
        "title": (
            info.get("title")
            or entry.get("title")
            or "Untitled"
        ),
        "url": (
            info.get("webpage_url")
            or video_url
        ),
        "view_count": (
            int(view_count)
            if view_count is not None
            else None
        ),
        "duration": (
            float(duration)
            if duration is not None
            else None
        ),
        "channel": (
            info.get("channel")
            or info.get("uploader")
            or entry.get("channel")
            or "Unknown"
        ),
        "upload_date": (
            info.get("upload_date")
            or entry.get("upload_date")
        ),
    }


def collect_channel_metadata(
    channel_url: str,
    log_callback=None,
) -> list:
    entries = extract_channel_entries(
        channel_url,
        log_callback,
    )

    metadata = []

    detail_options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
        "extractor_retries": 2,
    }

    with yt_dlp.YoutubeDL(detail_options) as downloader:
        for index, entry in enumerate(entries, start=1):
            try:
                video = extract_complete_video_metadata(
                    entry,
                    downloader,
                )

                if not video:
                    continue

                metadata.append(video)

                views = video["view_count"]

                formatted_views = (
                    f"{views:,}"
                    if views is not None
                    else "Unavailable"
                )

                emit(
                    f"Video {index}/{len(entries)}: "
                    f"{video['title']} | "
                    f"{formatted_views} views",
                    log_callback,
                )

            except Exception as error:
                emit(
                    f"Metadata extraction failed for "
                    f"{entry.get('id', 'unknown')}: {error}",
                    log_callback,
                )

    return metadata


def calculate_channel_mean(
    videos: list,
) -> Optional[float]:
    valid_views = [
        float(video["view_count"])
        for video in videos
        if (
            isinstance(video.get("view_count"), (int, float))
            and video["view_count"] > 0
        )
    ]

    if not valid_views:
        return None

    return sum(valid_views) / len(valid_views)


def format_views(value: float) -> str:
    value = float(value)

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.1f}K"

    return f"{value:,.0f}"


def get_outlier_candidates(
    videos: list,
    channel_mean: float,
    min_multiplier: float,
    max_multiplier: Optional[float],
    processed_ids: set,
    max_duration: int,
) -> list:
    candidates = []

    for video in videos:
        video_id = str(video.get("id") or "")

        if not video_id:
            continue

        if video_id in processed_ids:
            continue

        views = video.get("view_count")
        duration = video.get("duration")

        if not isinstance(views, (int, float)):
            continue

        if views <= 0:
            continue

        if not isinstance(duration, (int, float)):
            continue

        if duration < MIN_VIDEO_DURATION:
            continue

        if duration > max_duration:
            continue

        ratio = float(views) / channel_mean

        if ratio < min_multiplier:
            continue

        if (
            max_multiplier is not None
            and ratio > max_multiplier
        ):
            continue

        candidates.append({
            **video,
            "view_count": int(views),
            "duration": float(duration),
            "channel_mean": float(channel_mean),
            "outlier_ratio": round(ratio, 4),
        })

    candidates.sort(
        key=lambda item: item["outlier_ratio"],
        reverse=True,
    )

    return candidates


def discover_outliers(
    niche_key: str,
    niche_config: dict,
    max_duration: int,
    log_callback=None,
) -> list:
    processed_ids = load_processed_history()

    min_multiplier = float(
        niche_config.get(
            "min_outlier_multiplier",
            DEFAULT_MIN_MULTIPLIER,
        )
    )

    max_multiplier = niche_config.get(
        "max_outlier_multiplier"
    )

    if max_multiplier is not None:
        max_multiplier = float(max_multiplier)

    all_candidates = []
    channels = niche_config["target_channels"]

    for channel_index, channel_url in enumerate(
        channels,
        start=1,
    ):
        emit(
            f"[{niche_key}] Channel "
            f"{channel_index}/{len(channels)}",
            log_callback,
        )

        try:
            videos = collect_channel_metadata(
                channel_url,
                log_callback,
            )

            if not videos:
                emit(
                    f"No videos found for {channel_url}",
                    log_callback,
                )
                continue

            channel_mean = calculate_channel_mean(videos)

            if channel_mean is None:
                emit(
                    f"No usable view counts for {channel_url}",
                    log_callback,
                )
                continue

            emit(
                f"Channel average: "
                f"{format_views(channel_mean)} "
                f"across {len(videos)} inspected uploads",
                log_callback,
            )

            candidates = get_outlier_candidates(
                videos=videos,
                channel_mean=channel_mean,
                min_multiplier=min_multiplier,
                max_multiplier=max_multiplier,
                processed_ids=processed_ids,
                max_duration=max_duration,
            )

            for candidate in candidates:
                candidate["source_channel"] = channel_url

                all_candidates.append(candidate)

                emit(
                    f"[{niche_config['display_name']}] "
                    f"Found "
                    f"{candidate['outlier_ratio']:.2f}x Outlier: "
                    f"\"{candidate['title']}\" "
                    f"({format_views(candidate['view_count'])} views "
                    f"vs "
                    f"{format_views(candidate['channel_mean'])} avg)",
                    log_callback,
                )

        except Exception as error:
            emit(
                f"Channel scan failed: "
                f"{channel_url} | {error}",
                log_callback,
            )

    all_candidates.sort(
        key=lambda item: item["outlier_ratio"],
        reverse=True,
    )

    return all_candidates


def find_downloaded_mp4(
    download_directory: Path,
) -> Optional[Path]:
    candidates = [
        path
        for path in download_directory.glob("*.mp4")
        if path.is_file() and path.stat().st_size > 0
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda path: path.stat().st_size,
    )


def find_english_vtt(
    download_directory: Path,
) -> Optional[Path]:
    candidates = [
        path
        for path in download_directory.glob("*.vtt")
        if path.is_file() and path.stat().st_size > 0
    ]

    if not candidates:
        return None

    english_candidates = [
        path
        for path in candidates
        if re.search(
            r"\.en(?:[-_.]|$)",
            path.name.lower(),
        )
    ]

    if english_candidates:
        return english_candidates[0]

    return candidates[0]


def replace_file_atomically(
    source: Path,
    destination: Path,
) -> None:
    temporary_destination = destination.with_name(
        destination.name + ".tmp"
    )

    shutil.copy2(
        source,
        temporary_destination,
    )

    os.replace(
        temporary_destination,
        destination,
    )


def download_source(
    candidate: dict,
    output_directory: Path,
    platform_name: str,
    niche_key: str,
    log_callback=None,
) -> dict:
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.TemporaryDirectory(
        prefix=".download_",
        dir=str(output_directory),
    ) as temporary_directory:
        work_directory = Path(temporary_directory)

        output_template = str(
            work_directory / "%(id)s.%(ext)s"
        )

        options = {
            "format": (
                "bv*[height<=1080]+ba/"
                "b[height<=1080]"
            ),
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4",
                }
            ],
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [
                "en",
                "en-US",
                "en-GB",
            ],
            "subtitlesformat": "vtt",
            "skip_download": False,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "noplaylist": True,
        }

        emit(
            f"Downloading source: {candidate['title']}",
            log_callback,
        )

        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([
                candidate["url"]
            ])

        downloaded_video = find_downloaded_mp4(
            work_directory
        )

        if downloaded_video is None:
            raise RuntimeError(
                "No MP4 video was produced by the download."
            )

        downloaded_transcript = find_english_vtt(
            work_directory
        )

        if downloaded_transcript is None:
            raise RuntimeError(
                "No English VTT subtitle file was produced."
            )

        final_video = (
            output_directory / "source_video.mp4"
        )

        final_transcript = (
            output_directory / "source_transcript.vtt"
        )

        replace_file_atomically(
            downloaded_video,
            final_video,
        )

        replace_file_atomically(
            downloaded_transcript,
            final_transcript,
        )

        metadata = {
            "platform": platform_name,
            "niche": niche_key,
            "video_id": candidate["id"],
            "title": candidate["title"],
            "source_url": candidate["url"],
            "source_channel": candidate["source_channel"],
            "channel_name": candidate.get("channel"),
            "view_count": candidate["view_count"],
            "channel_mean": round(
                candidate["channel_mean"],
                2,
            ),
            "outlier_ratio": candidate["outlier_ratio"],
            "duration_sec": candidate["duration"],
            "upload_date": candidate.get("upload_date"),
            "downloaded_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "video_file": final_video.name,
            "transcript_file": final_transcript.name,
            "video_resolution_limit": MAX_VIDEO_HEIGHT,
        }

        write_json(
            output_directory / "source_meta.json",
            metadata,
        )

        save_processed_video(
            str(candidate["id"])
        )

        emit(
            f"SUCCESS: {candidate['id']} downloaded "
            f"and recorded. "
            f"Outlier ratio: "
            f"{candidate['outlier_ratio']:.2f}x",
            log_callback,
        )

        return metadata


def run_niche_scraper(
    platform_name: str,
    niche_key: str,
    log_callback: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    emit(
        f"Starting niche scan: "
        f"{platform_name} / {niche_key}",
        log_callback,
    )

    platform_config = load_platform_config(
        platform_name
    )

    niche_config = load_niche_config(
        niche_key
    )

    max_duration = int(
        platform_config.get(
            "max_duration_sec",
            58,
        )
    )

    output_directory = (
        DATA_DIR
        / "raw"
        / platform_name
        / niche_key
    )

    candidates = discover_outliers(
        niche_key=niche_key,
        niche_config=niche_config,
        max_duration=max_duration,
        log_callback=log_callback,
    )

    if not candidates:
        emit(
            f"No eligible unprocessed outliers found "
            f"for {niche_key}.",
            log_callback,
        )
        return None

    winner = candidates[0]

    emit(
        f"Selected #1 winner: "
        f"{winner['title']} | "
        f"{winner['outlier_ratio']:.2f}x",
        log_callback,
    )

    return download_source(
        candidate=winner,
        output_directory=output_directory,
        platform_name=platform_name,
        niche_key=niche_key,
        log_callback=log_callback,
    )


def run_platform(
    platform_name: str,
    log_callback=None,
) -> dict:
    results = {}

    for niche_key in NICHE_ORDER:
        try:
            results[niche_key] = run_niche_scraper(
                platform_name=platform_name,
                niche_key=niche_key,
                log_callback=log_callback,
            )

        except Exception as error:
            emit(
                f"ERROR: {platform_name}/{niche_key}: {error}",
                log_callback,
            )

            results[niche_key] = None

    return results


def run_enabled_platforms(
    log_callback=None,
) -> dict:
    results = {}

    for platform_config in load_platforms():
        if not platform_config.get("enabled", False):
            continue

        platform_name = platform_config.get("platform")

        if not platform_name:
            continue

        emit(
            f"========== PLATFORM: {platform_name} ==========",
            log_callback,
        )

        results[platform_name] = run_platform(
            platform_name,
            log_callback,
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Global Viral Engine - Phase 1 Outlier Scraper"
        )
    )

    parser.add_argument(
        "--platform",
        required=True,
        help="Platform key, such as youtube_shorts",
    )

    parser.add_argument(
        "--niche",
        choices=NICHE_ORDER,
        help="Optional niche to scan individually",
    )

    args = parser.parse_args()

    try:
        if args.niche:
            result = run_niche_scraper(
                platform_name=args.platform,
                niche_key=args.niche,
            )

            return 0 if result else 1

        results = run_platform(args.platform)

        return 0 if any(results.values()) else 1

    except Exception:
        logging.exception("Phase 1 scraper failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
