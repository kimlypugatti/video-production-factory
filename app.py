import importlib.util
import json
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.secrets import GEMINI_API_KEY


CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

PLATFORMS_FILE = CONFIG_DIR / "platforms.json"
NICHES_FILE = CONFIG_DIR / "niches.json"
HISTORY_FILE = DATA_DIR / "processed_history.json"

SCRAPER_FILE = (
    ROOT_DIR / "scripts" / "1_scraper.py"
)

NICHE_ORDER = [
    "irl_streaming",
    "strategy_and_skill_challenges",
    "business_podcasts",
]


st.set_page_config(
    page_title="Global Viral Engine",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
        .stApp {
            background-color: #0b1020;
            color: #f1f5f9;
        }

        [data-testid="stSidebar"] {
            background-color: #111827;
        }

        [data-testid="stMetric"] {
            background-color: #172033;
            border: 1px solid #273449;
            border-radius: 12px;
            padding: 16px;
        }

        .multiplier {
            display: inline-block;
            background: #123c32;
            color: #9ef0c5;
            border: 1px solid #28785b;
            border-radius: 8px;
            padding: 5px 10px;
            font-weight: 700;
        }

        .subtle {
            color: #94a3b8;
            font-size: 13px;
        }

        div[data-testid="stButton"] > button {
            border-radius: 8px;
            font-weight: 700;
        }

        div[data-testid="stDownloadButton"] > button {
            border-radius: 8px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_scraper_module():
    if not SCRAPER_FILE.exists():
        raise FileNotFoundError(
            f"Scraper not found: {SCRAPER_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "phase1_scraper",
        SCRAPER_FILE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Could not load scripts/1_scraper.py."
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


scraper = load_scraper_module()
run_niche_scraper = scraper.run_niche_scraper


def read_json(path: Path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

    temporary_path.replace(path)


def load_platforms():
    platforms = read_json(
        PLATFORMS_FILE,
        [],
    )

    if not isinstance(platforms, list):
        raise ValueError(
            "config/platforms.json must contain a JSON array."
        )

    return platforms


def load_niches():
    niches = read_json(
        NICHES_FILE,
        {},
    )

    if not isinstance(niches, dict):
        raise ValueError(
            "config/niches.json must contain a JSON object."
        )

    return niches


def load_history():
    history = read_json(
        HISTORY_FILE,
        [],
    )

    return history if isinstance(history, list) else []


def valid_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())

        return (
            parsed.scheme == "https"
            and parsed.netloc in {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
            }
        )

    except Exception:
        return False


def add_log(message: str) -> None:
    st.session_state.setdefault("logs", [])
    st.session_state.logs.append(message)
    st.session_state.logs = st.session_state.logs[-500:]


def format_number(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "N/A"


def get_niche_metadata(
    platform: str,
    niche: str,
):
    metadata_path = (
        RAW_DIR
        / platform
        / niche
        / "source_meta.json"
    )

    return read_json(
        metadata_path,
        None,
    )


def get_niche_video(
    platform: str,
    niche: str,
):
    video_path = (
        RAW_DIR
        / platform
        / niche
        / "source_video.mp4"
    )

    return video_path if video_path.exists() else None


def create_source_archive(platform: str) -> bytes:
    buffer = BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for niche in NICHE_ORDER:
            directory = RAW_DIR / platform / niche

            if not directory.exists():
                continue

            for path in directory.iterdir():
                if (
                    path.is_file()
                    and not path.name.startswith(".")
                ):
                    archive.write(
                        path,
                        arcname=f"{niche}/{path.name}",
                    )

    buffer.seek(0)
    return buffer.getvalue()


def render_platform_manager():
    st.sidebar.header("Platform Queue")

    platforms = load_platforms()

    for index, platform in enumerate(platforms):
        platform_name = platform.get(
            "platform",
            f"platform_{index}",
        )

        with st.sidebar.expander(
            platform_name.replace("_", " ").title(),
            expanded=False,
        ):
            with st.form(
                f"platform_form_{platform_name}"
            ):
                enabled = st.checkbox(
                    "Enabled",
                    value=bool(
                        platform.get("enabled", False)
                    ),
                    key=f"platform_enabled_{platform_name}",
                )

                max_duration = st.number_input(
                    "Maximum duration (seconds)",
                    min_value=15,
                    max_value=600,
                    value=int(
                        platform.get("max_duration_sec", 58)
                    ),
                    key=f"platform_duration_{platform_name}",
                )

                aspect_ratio = st.text_input(
                    "Aspect ratio",
                    value=platform.get("aspect_ratio", "9:16"),
                    key=f"platform_aspect_{platform_name}",
                )

                videos_per_niche = st.number_input(
                    "Videos per niche",
                    min_value=1,
                    max_value=20,
                    value=int(
                        platform.get("videos_per_niche", 4)
                    ),
                    key=f"platform_videos_{platform_name}",
                )

                save_platform = st.form_submit_button(
                    "Save Platform Settings",
                    use_container_width=True,
                )

            if save_platform:
                platforms[index]["enabled"] = enabled
                platforms[index]["max_duration_sec"] = int(
                    max_duration
                )
                platforms[index]["aspect_ratio"] = (
                    aspect_ratio.strip()
                )
                platforms[index]["videos_per_niche"] = int(
                    videos_per_niche
                )
                platforms[index]["total_batch_size"] = (
                    len(NICHE_ORDER) * int(videos_per_niche)
                )

                write_json(
                    PLATFORMS_FILE,
                    platforms,
                )

                st.sidebar.success(
                    f"{platform_name} saved."
                )
                st.rerun()

    with st.sidebar.expander("Add New Platform"):
        with st.form("add_platform_form"):
            new_platform = st.text_input(
                "Platform key",
                placeholder="instagram_reels",
            )

            new_duration = st.number_input(
                "Maximum duration",
                min_value=15,
                max_value=600,
                value=58,
            )

            new_aspect_ratio = st.selectbox(
                "Aspect ratio",
                ["9:16", "16:9", "1:1", "4:5"],
            )

            add_platform = st.form_submit_button(
                "Add Platform",
                use_container_width=True,
            )

        if add_platform:
            normalized_name = (
                new_platform.strip()
                .lower()
                .replace(" ", "_")
            )

            if not re.fullmatch(
                r"[a-z0-9_-]+",
                normalized_name,
            ):
                st.sidebar.error(
                    "Enter a valid platform key."
                )

            elif any(
                item.get("platform") == normalized_name
                for item in platforms
            ):
                st.sidebar.error(
                    "This platform already exists."
                )

            else:
                platforms.append({
                    "platform": normalized_name,
                    "enabled": False,
                    "max_duration_sec": int(new_duration),
                    "aspect_ratio": new_aspect_ratio,
                    "videos_per_niche": 4,
                    "total_batch_size": 12,
                })

                write_json(
                    PLATFORMS_FILE,
                    platforms,
                )

                st.sidebar.success(
                    f"{normalized_name} added."
                )
                st.rerun()


def render_niche_manager():
    niches = load_niches()

    st.sidebar.divider()
    st.sidebar.header("Niche & Creator Manager")

    available_niches = [
        niche
        for niche in NICHE_ORDER
        if niche in niches
    ]

    selected_niche = st.sidebar.selectbox(
        "Select niche",
        available_niches,
        format_func=lambda niche: niches[niche].get(
            "display_name",
            niche,
        ),
    )

    config = niches[selected_niche]

    with st.sidebar.form(
        f"niche_form_{selected_niche}"
    ):
        display_name = st.text_input(
            "Display name",
            value=config.get("display_name", selected_niche),
        )

        channel_persona = st.text_input(
            "Channel persona",
            value=config.get("channel_persona", ""),
        )

        min_multiplier = st.slider(
            "Minimum outlier multiplier",
            min_value=2.0,
            max_value=15.0,
            value=float(
                config.get("min_outlier_multiplier", 3.5)
            ),
            step=0.5,
        )

        channel_text = st.text_area(
            "Target channel URLs (one URL per line)",
            value="\n".join(
                config.get("target_channels", [])
            ),
            height=220,
        )

        save_niche = st.form_submit_button(
            "Save Niche Settings",
            use_container_width=True,
        )

    if save_niche:
        channel_urls = [
            line.strip()
            for line in channel_text.splitlines()
            if line.strip()
        ]

        invalid_urls = [
            url
            for url in channel_urls
            if not valid_youtube_url(url)
        ]

        if invalid_urls:
            st.sidebar.error(
                f"Invalid YouTube channel URL: {invalid_urls[0]}"
            )

        elif len(channel_urls) != len(set(channel_urls)):
            st.sidebar.error(
                "Duplicate channel URLs are not allowed."
            )

        else:
            niches[selected_niche]["display_name"] = (
                display_name.strip()
            )

            niches[selected_niche]["channel_persona"] = (
                channel_persona.strip()
            )

            niches[selected_niche]["min_outlier_multiplier"] = (
                float(min_multiplier)
            )

            niches[selected_niche]["target_channels"] = (
                channel_urls
            )

            write_json(
                NICHES_FILE,
                niches,
            )

            st.sidebar.success(
                "Niche configuration saved."
            )
            st.rerun()

    st.sidebar.caption(
        f"Configured creators: "
        f"{len(config.get('target_channels', []))}"
    )


def render_api_status():
    st.sidebar.divider()
    st.sidebar.header("Gemini Configuration")

    configured = bool(
        GEMINI_API_KEY
        and GEMINI_API_KEY != "PASTE_YOUR_GEMINI_API_KEY_HERE"
    )

    st.sidebar.caption(
        "GEMINI_API_KEY: "
        + ("Configured" if configured else "Not configured")
    )


def render_terminal(placeholder):
    logs = st.session_state.get("logs", [])

    content = (
        "\n".join(logs)
        if logs
        else "Waiting for pipeline execution..."
    )

    placeholder.code(
        content,
        language="text",
    )


def run_autonomous_pipeline(
    terminal_placeholder,
    progress_placeholder,
):
    platforms = load_platforms()

    active_platforms = [
        platform
        for platform in platforms
        if platform.get("enabled", False)
    ]

    if not active_platforms:
        st.warning(
            "No enabled platforms in the platform queue."
        )
        return

    total_steps = (
        len(active_platforms)
        * len(NICHE_ORDER)
    )

    completed_steps = 0

    progress_bar = progress_placeholder.progress(
        0,
        text="Initializing pipeline...",
    )

    def pipeline_log(message: str):
        add_log(message)
        render_terminal(terminal_placeholder)

    pipeline_log(
        "========== AUTONOMOUS PIPELINE STARTED =========="
    )

    for platform_config in active_platforms:
        platform_name = platform_config["platform"]

        pipeline_log(
            f"========== PLATFORM: {platform_name} =========="
        )

        for niche in NICHE_ORDER:
            pipeline_log(
                f"Starting niche: {niche}"
            )

            try:
                metadata = run_niche_scraper(
                    platform_name=platform_name,
                    niche_key=niche,
                    log_callback=pipeline_log,
                )

                if metadata:
                    pipeline_log(
                        f"WINNER: {metadata['title']} | "
                        f"{metadata['outlier_ratio']:.2f}x | "
                        f"{format_number(metadata['view_count'])} views"
                    )
                else:
                    pipeline_log(
                        f"No eligible unprocessed outlier found "
                        f"for {niche}."
                    )

            except Exception as error:
                pipeline_log(
                    f"ERROR: {platform_name}/{niche}: {error}"
                )

            completed_steps += 1

            progress_bar.progress(
                completed_steps / total_steps,
                text=(
                    f"Completed {completed_steps} "
                    f"of {total_steps} niche scans"
                ),
            )

    pipeline_log(
        "========== AUTONOMOUS PIPELINE COMPLETE =========="
    )

    progress_bar.progress(
        1.0,
        text="Pipeline run completed.",
    )

    st.success(
        "Autonomous Phase 1 pipeline completed."
    )


def render_outlier_card(
    platform: str,
    niche: str,
    niche_config: dict,
):
    st.subheader(
        niche_config.get("display_name", niche)
    )

    metadata = get_niche_metadata(
        platform,
        niche,
    )

    video_path = get_niche_video(
        platform,
        niche,
    )

    if not metadata:
        st.info(
            "No winning source recorded for this niche."
        )

        st.caption(
            "The Phase 1 scraper has not recorded a winner yet."
        )

    else:
        multiplier = float(
            metadata.get("outlier_ratio", 0)
        )

        st.markdown(
            f"""
            <span class="multiplier">
                {multiplier:.2f}x OUTLIER
            </span>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"**{metadata.get('title', 'Untitled')}**"
        )

        st.caption(
            f"Views: {format_number(metadata.get('view_count'))}"
        )

        st.caption(
            f"Channel mean: "
            f"{format_number(metadata.get('channel_mean'))}"
        )

        st.caption(
            f"Duration: "
            f"{metadata.get('duration_sec', 'N/A')} seconds"
        )

        source_url = metadata.get("source_url")

        if source_url:
            st.markdown(
                f"[Open source video]({source_url})"
            )

        source_channel = metadata.get("source_channel")

        if source_channel:
            st.caption(
                f"Channel: {source_channel}"
            )

        if video_path:
            st.video(str(video_path))

        transcript_path = (
            RAW_DIR
            / platform
            / niche
            / "source_transcript.vtt"
        )

        if transcript_path.exists():
            with st.expander("English transcript"):
                transcript = transcript_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )

                st.text_area(
                    "Transcript",
                    transcript,
                    height=180,
                    key=f"transcript_{platform}_{niche}",
                )

        with st.expander("Complete source metadata"):
            st.json(metadata)

    st.divider()


# =========================================================
# MAIN APPLICATION
# =========================================================

st.title(
    "Global Viral Engine — 3-Niche Command Center"
)

st.markdown(
    """
    <p class="subtle">
        Phase 1 · Source discovery · Outlier detection ·
        Local source assets
    </p>
    """,
    unsafe_allow_html=True,
)


render_platform_manager()
render_niche_manager()
render_api_status()


platforms = load_platforms()
niches = load_niches()
history = load_history()

active_platforms = [
    platform
    for platform in platforms
    if platform.get("enabled", False)
]

active_platform_names = [
    platform["platform"]
    for platform in active_platforms
]


# =========================================================
# BATCH ARCHITECTURE SUMMARY
# =========================================================

st.divider()
st.header("Batch Architecture Summary")

summary_col1, summary_col2, summary_col3, summary_col4 = (
    st.columns(4)
)

summary_col1.metric(
    "Active Platforms",
    len(active_platforms),
)

summary_col2.metric(
    "Active Niches",
    len(NICHE_ORDER),
)

summary_col3.metric(
    "Videos Per Platform",
    12,
)

summary_col4.metric(
    "Processed Sources",
    len(history),
)

st.markdown("**Active Platform Queue**")

if active_platform_names:
    st.write(
        " · ".join(active_platform_names)
    )
else:
    st.warning(
        "No platforms are currently enabled."
    )

st.info(
    "3 niches × 4 planned variations = 12 videos per platform. "
    "Phase 1 discovers one winning source per niche."
)


# =========================================================
# PIPELINE EXECUTION
# =========================================================

st.divider()
st.header("Autonomous Pipeline")

st.caption(
    "Enabled platforms are processed sequentially. "
    "Each platform scans all three niches in sequence."
)

terminal_placeholder = st.empty()
progress_placeholder = st.empty()

if st.button(
    "START AUTONOMOUS PIPELINE",
    type="primary",
    use_container_width=True,
):
    run_autonomous_pipeline(
        terminal_placeholder,
        progress_placeholder,
    )

render_terminal(terminal_placeholder)


# =========================================================
# THREE-COLUMN OUTLIER PREVIEW
# =========================================================

st.divider()
st.header("Scraped Outliers Preview")

available_platforms = [
    platform.get("platform")
    for platform in platforms
    if platform.get("platform")
]

if available_platforms:
    selected_preview_platform = st.selectbox(
        "Select platform",
        available_platforms,
        key="preview_platform",
    )

    preview_columns = st.columns(3)

    for index, niche in enumerate(NICHE_ORDER):
        with preview_columns[index]:
            if niche not in niches:
                st.warning(
                    f"Missing configuration for {niche}."
                )
                continue

            render_outlier_card(
                selected_preview_platform,
                niche,
                niches[niche],
            )

    st.divider()
    st.subheader("Research Asset Export")

    archive_data = create_source_archive(
        selected_preview_platform
    )

    st.download_button(
        label="Download Phase 1 Research ZIP",
        data=archive_data,
        file_name=(
            f"{selected_preview_platform}_phase1_research.zip"
        ),
        mime="application/zip",
        use_container_width=True,
    )

else:
    st.info(
        "Add a platform to the platform queue to view results."
    )


# =========================================================
# PROCESSED HISTORY
# =========================================================

st.divider()
st.header("Processed Video History")

history = load_history()

if history:
    history_frame = pd.DataFrame({
        "Video ID": [
            item.get("video_id")
            if isinstance(item, dict)
            else item
            for item in history
        ],
    })

    st.dataframe(
        history_frame,
        use_container_width=True,
        hide_index=True,
    )

else:
    st.info(
        "No videos have been processed yet."
    )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "Global Viral Engine · Phase 1 · "
    "Sequential multi-platform source discovery"
)
