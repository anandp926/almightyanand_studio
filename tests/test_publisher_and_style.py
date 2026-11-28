from datetime import datetime, timezone

from agents import media_dispatcher, publisher
from agents.media_dispatcher import MediaDispatcher, active_visual_style
from agents.publisher import Publisher


class _Call:
    def __init__(self, result=None):
        self.result = result

    def execute(self):
        return self.result

    def next_chunk(self):
        return None, self.result


class FakeYouTube:
    def __init__(self):
        self.bodies, self.comments = [], []

    def videos(self):
        return self

    def commentThreads(self):
        return _Comments(self)

    def insert(self, part=None, body=None, media_body=None):
        self.bodies.append(body)
        return _Call({"id": "vid1"})


class _Comments:
    def __init__(self, yt):
        self.yt = yt

    def insert(self, part=None, body=None):
        self.yt.comments.append(body)
        return _Call({})


def _publisher(monkeypatch, tmp_path):
    import googleapiclient.http

    yt = FakeYouTube()
    pub = Publisher()
    monkeypatch.setattr(pub, "_get_youtube_service", lambda: yt)
    monkeypatch.setattr(googleapiclient.http, "MediaFileUpload", lambda *a, **kw: object())
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return pub, yt, video


def test_scheduled_upload_is_private_with_publish_time_and_no_comment(monkeypatch, tmp_path):
    pub, yt, video = _publisher(monkeypatch, tmp_path)
    when = datetime(2026, 11, 25, 7, 0, tzinfo=timezone.utc)
    rec = pub.publish_youtube_short(video, "Title", "desc", top_comment="hi", publish_at=when)
    assert yt.bodies[0]["status"] == {
        "privacyStatus": "private", "publishAt": "2026-11-25T07:00:00.000Z", "selfDeclaredMadeForKids": False,
    }
    assert yt.comments == [] and rec.scheduled_for == "2026-11-25T07:00:00.000Z"


def test_immediate_upload_is_public_and_posts_the_comment(monkeypatch, tmp_path):
    pub, yt, video = _publisher(monkeypatch, tmp_path)
    rec = pub.publish_youtube_short(video, "Title", "desc", top_comment="hi")
    assert yt.bodies[0]["status"]["privacyStatus"] == "public" and "publishAt" not in yt.bodies[0]["status"]
    assert len(yt.comments) == 1 and rec.scheduled_for == "" and rec.comment_posted


def test_private_mode_is_never_scheduled_to_go_public(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "private")
    pub, yt, video = _publisher(monkeypatch, tmp_path)
    pub.publish_youtube_short(video, "Title", "desc", publish_at=datetime(2026, 11, 25, 7, 0, tzinfo=timezone.utc))
    assert yt.bodies[0]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}


def test_one_fixed_style_with_the_host_corner_kept_clear(monkeypatch):
    name, suffix = active_visual_style()
    assert name == "stylised_3d" and "stylised 3D render" in suffix and "empty uncluttered lower left corner" in suffix
    assert MediaDispatcher().style_suffix == suffix

    monkeypatch.setenv("VISUAL_STYLE", "anime")
    assert active_visual_style()[0] == "anime" and "anime illustration" in active_visual_style()[1]

    monkeypatch.setenv("VISUAL_STYLE", "does_not_exist")
    assert active_visual_style()[0] == "stylised_3d"

    monkeypatch.setenv("HOST_ENABLED", "false")
    assert "lower left" not in active_visual_style()[1]
