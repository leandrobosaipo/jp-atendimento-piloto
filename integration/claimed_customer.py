"""Read identity only while the original Dell collector claim belongs to this execution."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import urllib.parse
import urllib.request


class ClaimClosed(ValueError):
    pass


def _private_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(fd)
        if not (stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_mode & 0o077 == 0
                and st.st_nlink == 1 and st.st_size <= 131072):
            raise ClaimClosed("source_config_permissions")
        return json.load(stream)


def dell_reader(queue_path, workflow, epoch, instance):
    # Reuse the installed fixed-path AppBarber reader. It disables redirects and proxies.
    path = "/usr/local/libexec/jp-core-task-reads/task_source.py"
    spec = importlib.util.spec_from_file_location("jp_installed_task_source", path)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    def directory():
        cfg = _private_config("/etc/atendimento-piloto-integracao/appbarber-readonly.json")
        return cfg["source_generation"], source.official(cfg, "clients", {})
    def lookup(numbers):
        if len(numbers) != 1 or not re.fullmatch(r"55[1-9][0-9]{9,10}", numbers[0]):
            raise ClaimClosed("individual_lookup_required")
        cfg = _private_config("/etc/jp-evolution-readonly/access.json")
        if cfg["instance"] != instance or cfg["origin"] != "https://evolution.codigo5.com.br":
            raise ClaimClosed("wrong_provider_instance")
        url = cfg["origin"] + "/chat/whatsappNumbers/" + urllib.parse.quote(instance, safe="")
        request = urllib.request.Request(url, data=json.dumps({"numbers":numbers}).encode(),
            headers={"Content-Type":"application/json", "apikey":cfg["token"],
                     "CF-Access-Client-Id":cfg["client_id"], "CF-Access-Client-Secret":cfg["client_secret"]})
        opener = urllib.request.build_opener(source.NoRedirect(), urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=12) as response:
            raw = response.read(32769)
            if response.status != 200 or response.geturl() != url or len(raw) > 32768:
                raise ClaimClosed("lookup_unconfirmed")
        return json.loads(raw)
    return ClaimedDirectory(queue_path, workflow, epoch, directory, lookup)


class ClaimedDirectory:
    def __init__(self, queue_path, workflow, epoch, directory, lookup):
        self.queue_uri = Path(queue_path).resolve(strict=True).as_uri() + "?mode=ro"
        self.workflow, self.epoch = workflow, epoch
        self.directory, self.lookup = directory, lookup

    def check(self, record, workflow, execution, epoch):
        if not (workflow == self.workflow and epoch == self.epoch and
                re.fullmatch(r"[a-f0-9]{64}", record) and re.fullmatch(r"[0-9]{1,20}", execution)):
            raise ClaimClosed("claim_scope_changed")
        with closing(sqlite3.connect(self.queue_uri, uri=True, timeout=2)) as db:
            row = db.execute("""SELECT r.state,r.execution,r.epoch,o.epoch,o.fixture
                             FROM receipts r JOIN owner o ON o.slot=1 WHERE r.id=?""", (record,)).fetchone()
        if not row or row[:4] != ("claimed", execution, epoch, epoch) or row[4] not in (None, record):
            raise ClaimClosed("claim_closed")

    def decision(self, resolve, address_hash, record, workflow, execution, epoch):
        scope = (record, workflow, execution, epoch)
        self.check(*scope)
        generation, directory = self.directory()
        if type(generation) is not int or generation <= 0:
            raise ClaimClosed("unverified_directory_generation")
        def lookup(numbers):
            self.check(*scope)
            return self.lookup(numbers)
        decision = resolve(directory, [address_hash], lookup)[address_hash]
        self.check(*scope)
        return decision, generation, len(directory["data"])
