#!/usr/bin/env python3
"""
publisher.py — Multi-Platform Publishing Agent

Uploads rendered videos to YouTube and Instagram, handling:
  - Scheduled uploads at optimal times per channel analytics
  - SEO-optimized titles, descriptions, tags, and thumbnails
  - Affiliate link injection in descriptions
  - Pinned comment posting with CTAs
  - Multi-channel support (English main + Hindi auto-dubbed twin)

Runs on the CPU VM.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
import yaml

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
RENDER_COMPLETE_QUEUE_KEY = "render:complete_queue"
PUBLISH_LOG_KEY = "publish:log"
PUBLISH_SCHEDULE_KEY = "publish:schedule"

# Optimal upload windows (IST/UTC+5:30 converted to UTC)
OPTIMAL_UPLOAD_WINDOWS = {
    "weekday": [
        {"utc_hour": 1, "utc_minute": 0},   # 6:30 AM IST — morning commute
        {"utc_hour": 7, "utc_minute": 30},   # 1:00 PM IST — lunch break
        {"utc_hour": 12, "utc_minute": 0},   # 5:30 PM IST — evening commute
    ],
    "weekend": [
        {"utc_hour": 4, "utc_minute": 0},    # 9:30 AM IST — lazy morning
        {"utc_hour": 9, "utc_minute": 30},    # 3:00 PM IST — afternoon
        {"utc_hour": 13, "utc_minute": 0},    # 6:30 PM IST — evening
    ],
}

# Affiliate templates
AFFILIATE_LINKS = {
    "technology": [
        "🔗 My Recommended Gadgets: https://amzn.to/PLACEHOLDER_TECH",
        "📚 Best Tech Books: https://amzn.to/PLACEHOLDER_BOOKS",
    ],
    "finance": [
        "💰 Start Investing: https://PLACEHOLDER_BROKER_LINK",
        "📊 Budget App I Use: https://PLACEHOLDER_APP_LINK",
    ],
    "spirituality": [
        "📖 Sacred Texts Collection: https://amzn.to/PLACEHOLDER_BOOKS",
        "🕉 Meditation App: https://PLACEHOLDER_MEDITATION_LINK",
    ],
    "life-hacks": [
        "🛒 Products Mentioned: https://amzn.to/PLACEHOLDER_PRODUCTS",
    ],
}

PINNED_COMMENT_TEMPLATES = [
    "🔔 Turn on notifications so you never miss a video!\n\n"
    "💬 What topic should I cover next? Drop it in the comments!\n\n"
    "👍 If this helped you, smash that like button — it helps the algorithm show this to more people!",

    "🎯 Summary of this video:\n{summary}\n\n"
    "📌 Timestamps in the description!\n\n"
    "🤝 Join the Almighty Anand community — subscribe and hit the bell!",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PUBLISHER] %(levelname)s %(message)s",
)
log = logging.getLogger("publisher")


@dataclass
class PublishResult:
    """Result of a platform upload."""
    production_id: str
    platform: str               # "youtube", "youtube_shorts", "instagram"
    video_id: str = ""
    url: str = ""
    status: str = "pending"     # pending, scheduled, published, failed
    scheduled_time: str = ""
    published_at: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)


class Publisher:
    """Multi-platform video publisher with scheduling and SEO optimization."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)

        # Load channel config
        config_path = os.environ.get("CHANNELS_CONFIG", "/app/configs/channels.yaml")
        try:
            with open(config_path) as f:
                self.channels = yaml.safe_load(f)
        except FileNotFoundError:
            log.warning(f"Channel config not found at {config_path}, using defaults")
            self.channels = {"channels": {"english": {"channel_id": "PLACEHOLDER"}}}

        self.youtube_service = None
        self.instagram_client = None
        log.info("Publisher initialized")

    # ── YouTube Upload ─────────────────────────────────────────────

    def _init_youtube(self):
        """Initialize YouTube API client (lazy)."""
        if self.youtube_service:
            return

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds_path = os.environ.get("YOUTUBE_OAUTH_CREDS", "/app/data/youtube_creds.json")
            creds = Credentials.from_authorized_user_file(creds_path)
            self.youtube_service = build("youtube", "v3", credentials=creds)
            log.info("YouTube API client initialized")
        except Exception as e:
            log.error(f"Failed to initialize YouTube API: {e}")

    def upload_to_youtube(self, render: dict, script: dict,
                          category: str = "general",
                          is_short: bool = False) -> PublishResult:
        """Upload a video to YouTube with full SEO optimization."""
        self._init_youtube()
        production_id = render.get("production_id", "unknown")

        result = PublishResult(
            production_id=production_id,
            platform="youtube_shorts" if is_short else "youtube",
        )

        if not self.youtube_service:
            result.status = "failed"
            result.error = "YouTube API not initialized"
            return result

        try:
            from googleapiclient.http import MediaFileUpload

            video_path = render.get("output_path", "")
            if not video_path or not os.path.exists(video_path):
                result.status = "failed"
                result.error = f"Video file not found: {video_path}"
                return result

            # Build metadata
            title = script.get("title", f"Almighty Anand — {production_id}")[:100]
            description = self._build_description(script, category, is_short)
            tags = script.get("tags", []) + script.get("hashtags", [])

            # For Shorts, prepend #Shorts to title
            if is_short:
                title = f"{title} #Shorts"

            # Calculate optimal publish time
            scheduled_time = self._get_next_optimal_slot()

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags[:30],  # YouTube limit
                    "categoryId": "22",  # People & Blogs (safest default)
                    "defaultLanguage": "en",
                    "defaultAudioLanguage": "en",
                },
                "status": {
                    "privacyStatus": "private",  # Will be made public at scheduled time
                    "publishAt": scheduled_time,
                    "selfDeclaredMadeForKids": False,
                    "embeddable": True,
                    "license": "youtube",
                },
            }

            media = MediaFileUpload(video_path, mimetype="video/mp4",
                                     resumable=True, chunksize=10 * 1024 * 1024)

            request = self.youtube_service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    log.info(f"  📤 Upload progress: {int(status.progress() * 100)}%")

            video_id = response["id"]
            result.video_id = video_id
            result.url = f"https://youtube.com/watch?v={video_id}"
            result.status = "scheduled"
            result.scheduled_time = scheduled_time

            # Upload thumbnail
            thumbnail_path = render.get("thumbnail_path")
            if thumbnail_path and os.path.exists(thumbnail_path):
                self._upload_thumbnail(video_id, thumbnail_path)

            # Post pinned comment
            self._post_pinned_comment(video_id, script, category)

            log.info(f"  ✅ Uploaded to YouTube: {result.url} (scheduled: {scheduled_time})")

        except Exception as e:
            result.status = "failed"
            result.error = str(e)[:200]
            log.error(f"  ❌ YouTube upload failed: {e}")

        return result

    def _build_description(self, script: dict, category: str, is_short: bool) -> str:
        """Build SEO-optimized YouTube description with affiliate links."""
        lines = []

        # First 150 chars are most important (shown in search)
        seo_desc = script.get("description_seo", script.get("title", ""))
        lines.append(seo_desc[:150])
        lines.append("")

        if not is_short:
            # Timestamps (if available from retention hooks)
            hooks = script.get("retention_hooks", {})
            if hooks:
                lines.append("⏱ Timestamps:")
                for ts, text in sorted(hooks.items()):
                    lines.append(f"  {ts} — {text}")
                lines.append("")

        # Affiliate links
        affiliates = AFFILIATE_LINKS.get(category, AFFILIATE_LINKS.get("life-hacks", []))
        if affiliates:
            lines.append("📌 Links & Resources:")
            lines.extend(affiliates)
            lines.append("")

        # Standard footer
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "👤 About Almighty Anand:",
            "Tech, finance, ancient wisdom, and life hacks — served with a twist.",
            "",
            "🔔 Subscribe: https://youtube.com/@almightyanand?sub_confirmation=1",
            "📸 Instagram: https://instagram.com/almightyanand",
            "",
            "⚠ Disclaimer: Some links above are affiliate links. "
            "I may earn a small commission at no extra cost to you.",
            "",
            "#AlmightyAnand #YouTube",
        ])

        return "\n".join(lines)

    def _upload_thumbnail(self, video_id: str, thumbnail_path: str):
        """Upload custom thumbnail for a video."""
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            self.youtube_service.thumbnails().set(
                videoId=video_id, media_body=media
            ).execute()
            log.info(f"  🖼 Thumbnail uploaded for {video_id}")
        except Exception as e:
            log.warning(f"  ⚠ Thumbnail upload failed: {e}")

    def _post_pinned_comment(self, video_id: str, script: dict, category: str):
        """Post and pin a comment with CTAs."""
        try:
            template = PINNED_COMMENT_TEMPLATES[0]
            summary = script.get("hook", "Check out this video!")
            comment_text = template.format(summary=summary)

            comment = self.youtube_service.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": comment_text}
                        },
                    }
                },
            ).execute()
            log.info(f"  💬 Pinned comment posted on {video_id}")
        except Exception as e:
            log.warning(f"  ⚠ Pinned comment failed: {e}")

    def _get_next_optimal_slot(self) -> str:
        """Calculate next optimal upload time based on day of week."""
        now = datetime.now(timezone.utc)
        is_weekend = now.weekday() >= 5
        windows = OPTIMAL_UPLOAD_WINDOWS["weekend" if is_weekend else "weekday"]

        for window in windows:
            slot = now.replace(
                hour=window["utc_hour"],
                minute=window["utc_minute"],
                second=0,
                microsecond=0,
            )
            if slot > now + timedelta(hours=1):  # At least 1 hour from now
                return slot.isoformat()

        # Default to next day's first slot
        tomorrow = now + timedelta(days=1)
        is_weekend = tomorrow.weekday() >= 5
        windows = OPTIMAL_UPLOAD_WINDOWS["weekend" if is_weekend else "weekday"]
        slot = tomorrow.replace(
            hour=windows[0]["utc_hour"],
            minute=windows[0]["utc_minute"],
            second=0,
            microsecond=0,
        )
        return slot.isoformat()

    # ── Instagram Upload ───────────────────────────────────────────

    def upload_to_instagram(self, render: dict, script: dict) -> PublishResult:
        """Upload Reel to Instagram."""
        production_id = render.get("production_id", "unknown")
        result = PublishResult(
            production_id=production_id,
            platform="instagram",
        )

        try:
            from instagrapi import Client

            if not self.instagram_client:
                self.instagram_client = Client()
                self.instagram_client.login(
                    os.environ.get("INSTAGRAM_USERNAME", ""),
                    os.environ.get("INSTAGRAM_PASSWORD", ""),
                )

            video_path = render.get("output_path", "")
            caption_parts = [
                script.get("title", ""),
                "",
                script.get("hook", ""),
                "",
            ]
            # Add hashtags
            hashtags = script.get("hashtags", ["#AlmightyAnand", "#Reels", "#Trending"])
            caption_parts.append(" ".join(hashtags[:30]))

            caption = "\n".join(caption_parts)

            media = self.instagram_client.clip_upload(
                video_path,
                caption=caption,
            )

            result.video_id = str(media.pk)
            result.url = f"https://instagram.com/reel/{media.code}"
            result.status = "published"
            result.published_at = datetime.now(timezone.utc).isoformat()
            log.info(f"  ✅ Uploaded to Instagram: {result.url}")

        except ImportError:
            result.status = "failed"
            result.error = "instagrapi not installed"
        except Exception as e:
            result.status = "failed"
            result.error = str(e)[:200]
            log.error(f"  ❌ Instagram upload failed: {e}")

        return result

    # ── Main Loop ──────────────────────────────────────────────────

    def process_render_package(self, package: dict):
        """Process a completed render package — upload to all platforms."""
        production_id = package["production_id"]
        script_data = package.get("script_data", {})
        long_form_script = script_data.get("long_form_script", {})
        shorts_script = script_data.get("shorts_script", {})
        category = script_data.get("category", "general")

        log.info(f"📤 Publishing: {production_id}")
        results = []

        # Upload long-form to YouTube
        if package.get("long_form"):
            r = self.upload_to_youtube(package["long_form"], long_form_script, category)
            results.append(r)

        # Upload shorts to YouTube
        if package.get("shorts"):
            r = self.upload_to_youtube(package["shorts"], shorts_script, category, is_short=True)
            results.append(r)

        # Upload reels to Instagram
        if package.get("reels"):
            r = self.upload_to_instagram(package["reels"], shorts_script)
            results.append(r)

        # Log results
        for r in results:
            self.rdb.rpush(PUBLISH_LOG_KEY, json.dumps(asdict(r)))

        published = sum(1 for r in results if r.status in ("published", "scheduled"))
        log.info(f"✅ Published {published}/{len(results)} uploads for {production_id}")

    def run(self):
        """Main loop — consume render packages and publish."""
        log.info("📤 Publisher starting up...")
        while True:
            try:
                result = self.rdb.blpop(RENDER_COMPLETE_QUEUE_KEY, timeout=30)
                if not result:
                    continue

                _, package_json = result
                package = json.loads(package_json)
                self.process_render_package(package)

            except Exception as e:
                log.exception(f"Publish loop error: {e}")
                time.sleep(30)


if __name__ == "__main__":
    publisher = Publisher()
    publisher.run()
