import hashlib
import io
import json
import zipfile

import httpx
import pytest

from cwagent import webdist

URL = "https://example.com/web-dist.zip"


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def transport(data: bytes, digest: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".sha256"):
            return httpx.Response(200, text=(digest or hashlib.sha256(data).hexdigest()) + "\n")
        return httpx.Response(200, content=data)

    return httpx.MockTransport(handler)


def test_download_replaces_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "old.js").write_text("old")
    data = make_zip({"index.html": "<html>", "assets/app.js": "js",
                     "BUILD.json": json.dumps({"commit": "abc1234", "built_at": "2026-11-14T10:00:00Z"})})
    info = webdist.download(dist, url=URL, transport=transport(data), progress=lambda m: None)
    assert info["commit"] == "abc1234"
    assert (dist / "index.html").exists() and (dist / "assets" / "app.js").exists() and not (dist / "old.js").exists()
    assert webdist.build_info(dist)["commit"] == "abc1234"
    assert not (tmp_path / "dist.new").exists() and not (tmp_path / "dist.old").exists()


@pytest.mark.parametrize("files,digest,msg", [
    ({"index.html": "<html>"}, "0" * 64, "校验失败"),
    ({"assets/app.js": "js"}, None, "内容不对"),
])
def test_download_rejects_bad_package_and_keeps_old(tmp_path, files, digest, msg):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("old")
    data = make_zip(files)
    with pytest.raises(RuntimeError, match=msg):
        webdist.download(dist, url=URL, transport=transport(data, digest), progress=lambda m: None)
    assert (dist / "index.html").read_text() == "old"
