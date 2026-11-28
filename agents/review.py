#!/usr/bin/env python3
"""
review.py — Approve or reject queued Shorts before they are published.

With REVIEW_REQUIRED=true in .env, the publish slots only upload Shorts you
have approved here. Watch the file under renders/ first.

    python -m agents.review list
    python -m agents.review take 1 "Mujhe lagta hai yeh students ke liye sabse kaam ka hai."
    python -m agents.review approve 1        # or: approve all
    python -m agents.review reject 2

`take` adds your own line to the Short and re-renders it (about as long as the
first render, and it needs the image and voice services). A Short with your
opinion in it is your content, not just an automated summary. Options:
    --tts "मुझे लगता है ..."    the same line in Devanagari, for a better AI voice
    --audio my_take.wav         your own recorded voice for this line
"""

import argparse
import logging
from pathlib import Path

from agents import orchestrator
from agents.editor_ffmpeg import RENDERS_DIR

log = logging.getLogger("review")


def list_queue() -> list:
    return orchestrator._load_queue()


def approve(position: str) -> int:
    """Marks one item (1-based) or 'all' as approved. Returns how many changed."""
    q = orchestrator._load_queue()
    targets = range(len(q)) if position == "all" else [int(position) - 1]
    changed = 0
    for i in targets:
        if 0 <= i < len(q) and not q[i].get("approved"):
            q[i]["approved"] = True
            changed += 1
    orchestrator._save_queue(q)
    return changed


def reject(position: str) -> dict:
    """Removes one item (1-based) from the queue and moves its render to renders/rejected/."""
    q = orchestrator._load_queue()
    index = int(position) - 1
    if not 0 <= index < len(q):
        raise IndexError(f"No queue item {position}")
    item = q.pop(index)
    orchestrator._save_queue(q)
    video = Path(item["video_path"])
    if video.exists():
        rejected_dir = RENDERS_DIR / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        video.rename(rejected_dir / video.name)
    return item


def main():
    parser = argparse.ArgumentParser(description="Review queued Shorts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Show the publish queue")
    p_approve = sub.add_parser("approve", help="Approve an item by number, or 'all'")
    p_approve.add_argument("position")
    p_reject = sub.add_parser("reject", help="Reject an item by number")
    p_reject.add_argument("position")
    p_take = sub.add_parser("take", help="Add your own line to an item and re-render it")
    p_take.add_argument("position", type=int)
    p_take.add_argument("text", help="Your line, in Roman Hinglish (shown as the caption)")
    p_take.add_argument("--tts", default="", help="The same line in Devanagari, for the AI voice")
    p_take.add_argument("--audio", default="", help="A recording of you saying the line (wav or mp3)")
    args = parser.parse_args()

    if args.command == "list":
        q = list_queue()
        if not q:
            print("Queue is empty.")
        for i, item in enumerate(q, 1):
            state = "APPROVED" if item.get("approved") else "waiting"
            print(f"{i}. [{state}] {item.get('title', '')}")
            print(f"     format: {item.get('format') or '-'} | your take: {item.get('creator_take') or 'none yet'}")
            print(f"     series: {item.get('series') or '-'} | voice: {item.get('voice_provider') or '-'} | "
                  f"{item.get('duration_s') or '?'}s | source: {item.get('source_url') or '-'}")
            print(f"     file: {item.get('video_path')}")
    elif args.command == "approve":
        print(f"Approved {approve(args.position)} item(s).")
    elif args.command == "take":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [REVIEW] %(levelname)s %(message)s")
        item = orchestrator.apply_creator_take(args.position, args.text, tts_text=args.tts, audio_path=args.audio)
        print(f"Take added and re-rendered: {item['video_path']}")
    elif args.command == "reject":
        item = reject(args.position)
        print(f"Rejected: {item.get('title', '')}")


if __name__ == "__main__":
    main()
