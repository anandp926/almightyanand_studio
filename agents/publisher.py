#!/usr/bin/env python3
"""
publisher.py — Live YouTube Shorts Publisher & Monetization Agent
"""

import argparse
import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PUBLISHER] %(levelname)s %(message)s",
)
log = logging.getLogger("publisher")

STUDIO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH = STUDIO_ROOT / "configs" / "youtube_token.json"


@dataclass
class PublishRecord:
    platform: str
    channel_name: str
    video_title: str
    status: str
    target_slot: str
    affiliate_links_included: int
    pinned_comment_queued: bool
    url: str
    video_id: str = ""


class Publisher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.channel_id = os.environ.get("YOUTUBE_CHANNEL_ID_EN", "UCiyowMaCWGFMjPka0TDamVg")
        self.privacy_status = os.environ.get("YOUTUBE_PRIVACY_STATUS", "public")

    def _get_youtube_service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not TOKEN_PATH.exists():
            raise FileNotFoundError(f"YouTube OAuth token missing at {TOKEN_PATH}")

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        if creds.expired and creds.refresh_token:
            log.info("🔄 Refreshing expired YouTube OAuth token...")
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())

        return build("youtube", "v3", credentials=creds)

    def publish_youtube_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        pinned_comment: str = "",
        tags: Optional[List[str]] = None,
        slot_label: str = "Live Slot",
    ) -> PublishRecord:
        if self.dry_run:
            return self.publish_dry_run()[0]

        from googleapiclient.http import MediaFileUpload

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Cannot publish missing render: {video_path}")

        safe_title = title.strip()
        if "#shorts" not in safe_title.lower():
            safe_title = f"{safe_title[:88]} #Shorts"
        safe_title = safe_title[:100]

        youtube = self._get_youtube_service()
        body = {
            "snippet": {
                "title": safe_title,
                "description": description,
                "tags": tags or ["Shorts", "AlmightyAnand", "AI", "Tech", "Hinglish"],
                "categoryId": "28",  # Science & Technology
                "defaultLanguage": "hi",
            },
            "status": {
                "privacyStatus": self.privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        log.info(f"📤 Uploading Short to YouTube: '{safe_title}' ({video_path.name})")
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response["id"]
        video_url = f"https://youtube.com/shorts/{video_id}"
        log.info(f"✅ Uploaded successfully: {video_url}")

        comment_posted = False
        if pinned_comment:
            try:
                youtube.commentThreads().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "topLevelComment": {"snippet": {"textOriginal": pinned_comment}},
                        }
                    },
                ).execute()
                comment_posted = True
                log.info(f"💬 Affiliate comment posted on {video_id}")
            except Exception as e:
                log.warning(f"⚠️ Video uploaded, but comment post failed: {e}")

        return PublishRecord(
            platform="YouTube Shorts (9:16)",
            channel_name="Almighty Anand",
            video_title=safe_title,
            status=f"PUBLISHED ({self.privacy_status.upper()})",
            target_slot=slot_label,
            affiliate_links_included=description.count("http"),
            pinned_comment_queued=comment_posted,
            url=video_url,
            video_id=video_id,
        )

    def publish_dry_run(self, production_id: str = "prod_mock_2026") -> list:
        log.info(f"📤 Running Publisher in --dry-run mode for {production_id}")
        return [
            asdict(PublishRecord(
                platform="YouTube Shorts (9:16)",
                channel_name="Almighty Anand",
                video_title=f"Mock Short {production_id} #Shorts",
                status="MOCK_SCHEDULED",
                target_slot="Dry-Run",
                affiliate_links_included=1,
                pinned_comment_queued=True,
                url="https://youtube.com/shorts/mock_short_01",
                video_id="mock_short_01",
            ))
        ]


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
        print(json.dumps(asdict(rec), indent=2))
    else:
        print(json.dumps(pub.publish_dry_run(), indent=2))


if __name__ == "__main__":
    main()