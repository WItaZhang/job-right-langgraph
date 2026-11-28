from uuid import uuid4

from job_right.hosted import WriteConflict
from job_right.web import create_app
from test_web import field


class MemoryBlobs:
    def __init__(self):
        self.saved = {}
        self.conflict_once = False

    def read(self, name):
        return self.saved.get(name, (None, None))

    def write(self, name, body, etag):
        if self.conflict_once:
            self.conflict_once = False
            raise WriteConflict
        assert self.read(name)[1] == etag
        self.saved[name] = body, uuid4().hex


def test_cold_workers_restore_both_databases_and_isolate_new_visitors():
    storage = MemoryBlobs()

    def app():
        app = create_app(None, hosted=storage, secret="test-key")
        app.config.update(TRUSTED_HOSTS=["localhost"])
        return app

    browser = app().test_client()
    form = browser.get("/")
    data = dict(token=field(form, "token"), proposal_id="cloud", feedback="Only show me remote roles.")
    assert browser.post("/reviews", data=data).status_code == 303
    cookie = browser.get_cookie("job_right_workspace")
    cold = app().test_client()
    cold.set_cookie(cookie.key, cookie.value)
    review = cold.get("/reviews/cloud")
    storage.conflict_once = True
    decision = dict(token=field(review, "token"), digest=field(review, "digest"))
    assert cold.post("/reviews/cloud/approve", data=decision).status_code == 303
    assert "Profile v2" in cold.get("/preferences").text
    other = app().test_client()
    assert other.get("/reviews/cloud").status_code == 404
    assert "Profile v1" in other.get("/preferences").text
    assert other.post("/reviews/cloud/approve", data=decision).status_code == 403
