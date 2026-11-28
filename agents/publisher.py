#!/usr/bin/env python3
"""
publisher.py — YouTube Shorts Publisher

Uploads a rendered Short and posts one engagement comment.

Notes:
  - The YouTube Data API cannot pin a comment; the comment is posted as an
    ordinary top-level comment and can be pinned by hand in YouTube Studio.
  - Links in Shorts descriptions and comments are not clickable, so neither
    should carry affiliate URLs. Posting the comment needs the
    youtube.force-ssl OAuth scope.
  - `publish_at` uploads the video as private with a scheduled go-live time
    (YouTube publishes it itself). Used by burst mode, where the VM is off at
    publish time. The comment is skipped for scheduled uploads.
"""

import argparse
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agents.settings import STUDIO_ROOT, env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PUBLISHER] %(levelname)s %(message)s",
)
log = logging.getLogger("publisher")

TOKEN_PATH = Path(env("YOUTUBE_TOKEN_PATH") or STUDIO_ROOT / "configs" / "youtube_token.json")


@dataclass
class PublishRecord:
    platform: str
    channel_name: str
    video_title: str
    status: str
    target_slot: str
    affiliate_links_included: int
    comment_posted: bool
    url: str
    video_id: str = ""
    scheduled_for: str = ""


def load_youtube_credentials():
    """OAuth credentials from the stored token, refreshed and saved if expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_PATH.exists():
        raise FileNotFoundError(f"YouTube OAuth token missing at {TOKEN_PATH}")

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    if creds.expired and creds.refresh_token:
        log.info("🔄 Refreshing expired YouTube OAuth token...")
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return creds


class Publisher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.privacy_status = env("YOUTUBE_PRIVACY_STATUS", "public")

    def _get_youtube_service(self):
        from googleapiclient.discovery import build

        return build("youtube", "v3", credentials=load_youtube_credentials())

    def publish_youtube_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        top_comment: str = "",
        tags: Optional[List[str]] = None,
        slot_label: str = "Live Slot",
        publish_at: Optional[datetime] = None,
    ) -> PublishRecord:
        if self.dry_run:
            return self.publish_dry_run()

        from googleapiclient.http import MediaFileUpload

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Cannot publish missing render: {video_path}")

        safe_title = title.strip()
        if "#shorts" not in safe_title.lower():
            safe_title = f"{safe_title[:88]} #Shorts"
        safe_title = safe_title[:100]

        # Scheduling only applies when the video is meant to go public.
        scheduled = publish_at is not None and self.privacy_status == "public"
        status = {"privacyStatus": self.privacy_status, "selfDeclaredMadeForKids": False}
        scheduled_for = ""
        if scheduled:
            scheduled_for = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            status = {"privacyStatus": "private", "publishAt": scheduled_for, "selfDeclaredMadeForKids": False}

        youtube = self._get_youtube_service()
        body = {
            "snippet": {
                "title": safe_title,
                "description": description,
                "tags": tags or ["Shorts", "AlmightyAnand", "Hinglish"],
                "categoryId": "28",  # Science & Technology
                "defaultLanguage": "hi",
            },
            "status": status,
        }

        mode = f"scheduled for {scheduled_for}" if scheduled else self.privacy_status
        log.info(f"📤 Uploading Short to YouTube: '{safe_title}' ({video_path.name}, {mode})")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response["id"]
        video_url = f"https://youtube.com/shorts/{video_id}"
        log.info(f"✅ Uploaded successfully: {video_url}")

        comment_posted = False
        if top_comment and not scheduled:
            try:
                youtube.commentThreads().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "topLevelComment": {"snippet": {"textOriginal": top_comment}},
                        }
                    },
                ).execute()
                comment_posted = True
                log.info(f"💬 Comment posted on {video_id} (not pinned; the API cannot pin)")
            except Exception as e:
                log.warning(f"⚠️ Video uploaded, but comment post failed: {e}")

        return PublishRecord(
            platform="YouTube Shorts (9:16)",
            channel_name="Almighty Anand",
            video_title=safe_title,
            status=f"SCHEDULED ({scheduled_for})" if scheduled else f"PUBLISHED ({self.privacy_status.upper()})",
            target_slot=slot_label,
            affiliate_links_included=description.count("http"),
            comment_posted=comment_posted,
            url=video_url,
            video_id=video_id,
            scheduled_for=scheduled_for,
        )

    def publish_dry_run(self, production_id: str = "prod_mock_2026") -> PublishRecord:
        log.info(f"📤 Running Publisher in --dry-run mode for {production_id}")
        return PublishRecord(
            platform="YouTube Shorts (9:16)",
            channel_name="Almighty Anand",
            video_title=f"Mock Short {production_id} #Shorts",
            status="MOCK_SCHEDULED",
            target_slot="Dry-Run",
            affiliate_links_included=0,
            comment_posted=False,
            url="https://youtube.com/shorts/mock_short_01",
            video_id="mock_short_01",
        )


def main():
    parser = argparse.ArgumentParser(description="Publisher Agent")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--video", type=Path, help="Path to MP4 to upload")
    parser.add_argument("--title", type=str, default="Almighty Anand Update #Shorts")
    parser.add_argument("--desc", type=str, default="Follow @almightyanand for daily Hinglish tech & AI breakdowns.")
    args = parser.parse_args()

    pub = Publisher(dry_run=args.dry_run)
    if args.video and not args.dry_run:
        rec = pub.publish_youtube_short(args.video, args.title, args.desc)
    else:
        rec = pub.publish_dry_run()
    print(json.dumps(asdict(rec), indent=2))


if __name__ == "__main__":
    main()
