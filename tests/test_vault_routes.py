"""HTTP-layer tests for the Key Vault (`/api/vault/*`) — lifecycle, redaction,
the locked-state (423) contract, and Smart-Password-Detection capture — all via
the test client against throwaway caches, so the real caches/Outlook/vault file
are never touched. Skipped wholesale when `cryptography` is absent."""

import shutil
import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import create_app, vault_crypto
from tests.factories import make_mail

_ISOLATED = (
    "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
    "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
    "PASSWORD_SETTINGS_FILE", "EXPERIMENTAL_FILE", "CUSTOMER_MATCH_FILE",
    "VAULT_FILE", "VAULT_INDEX_FILE", "VAULT_KEY_DPAPI_FILE",
)


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class VaultRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {name: getattr(config, name) for name in _ISOLATED}
        for name in _ISOLATED:
            setattr(config, name, Path(self._tmpdir) / name.lower())
        self.app = create_app()
        self.client = self.app.test_client()
        self.org = self.client.post("/api/organizations", json={"name": "Acme"}).get_json()

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(config, name, value)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _init(self):
        return self.client.post("/api/vault/init", json={"passphrase": "passphrase1"})

    def _seed_mail(self, sender_email):
        """One cached mail from ``sender_email`` whose body holds a detectable
        password (matches the seeded default patterns / rules)."""
        self.app.extensions["mail_store"].add_mails([
            make_mail(id="P", subject="creds", sender_email=sender_email,
                      attachments=[], body="Hello,\nPassword:\n Hunter2xZ\nRegards"),
        ])

    def test_status_progression(self):
        self.assertFalse(self.client.get("/api/vault/status").get_json()["initialized"])
        self._init()
        s = self.client.get("/api/vault/status").get_json()
        self.assertTrue(s["initialized"] and s["unlocked"])

    def test_init_rejects_short_passphrase(self):
        self.assertEqual(self.client.post("/api/vault/init", json={"passphrase": "x"}).status_code, 400)

    def test_unlock_wrong_passphrase_401(self):
        self._init()
        self.client.post("/api/vault/lock")
        self.assertEqual(
            self.client.post("/api/vault/unlock", json={"passphrase": "nope"}).status_code, 401)
        self.assertEqual(
            self.client.post("/api/vault/unlock", json={"passphrase": "passphrase1"}).status_code, 200)

    def test_entries_redacted_and_reveal(self):
        self._init()
        add = self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": "Portal", "secret": "p@ss"}).get_json()
        self.assertNotIn("secret", add)
        self.assertTrue(add["has_secret"])
        listed = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertNotIn("secret", listed[self.org["id"]][0])
        revealed = self.client.post(f"/api/vault/entries/{add['id']}/reveal").get_json()
        self.assertEqual(revealed["secret"], "p@ss")

    def test_locked_routes_return_423(self):
        self._init()
        self.client.post("/api/vault/lock")
        self.assertEqual(self.client.get("/api/vault/entries").status_code, 423)
        self.assertEqual(self.client.post("/api/vault/entries",
                         json={"org_id": self.org["id"], "secret": "x"}).status_code, 423)

    def test_scan_auto_captures_into_senders_org(self):
        self._init()
        self.client.post(f"/api/organizations/{self.org['id']}/domains",
                         json={"domain": "acme.com", "role": "member"})
        self._seed_mail("bob@acme.com")
        scan = self.client.post("/api/passwords/scan").get_json()
        self.assertEqual(scan["vault_captured"], 1)
        self.assertFalse(scan["vault_locked"])
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        org_keys = entries[self.org["id"]]
        self.assertEqual(org_keys[0]["kind"], "temporary")
        self.assertEqual(org_keys[0]["source_email"], "bob@acme.com")

    def test_scan_parks_unresolved_sender_under_unassigned(self):
        self._init()
        self._seed_mail("x@nowhere.test")
        self.client.post("/api/passwords/scan")
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertIn(config.VAULT_UNASSIGNED_ORG_ID, entries)
        self.assertNotIn(self.org["id"], entries)

    def test_assigning_domain_rehomes_unassigned_capture(self):
        self._init()
        self._seed_mail("bob@acme.com")
        self.client.post("/api/passwords/scan")           # parked under Unassigned
        # Map the domain afterwards: the parked capture must move to the org.
        self.client.post(f"/api/organizations/{self.org['id']}/domains",
                         json={"domain": "acme.com", "role": "member"})
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertNotIn(config.VAULT_UNASSIGNED_ORG_ID, entries)
        self.assertEqual(entries[self.org["id"]][0]["source_email"], "bob@acme.com")

    def test_locked_scan_queues_then_unlock_flushes(self):
        self._init()
        self.client.post(f"/api/organizations/{self.org['id']}/domains",
                         json={"domain": "acme.com", "role": "member"})
        self._seed_mail("bob@acme.com")
        self.client.post("/api/vault/lock")
        scan = self.client.post("/api/passwords/scan").get_json()
        self.assertTrue(scan["vault_locked"])
        self.assertEqual(scan["vault_pending"], 1)
        self.assertEqual(scan["vault_captured"], 0)
        # Unlock: the queued capture is recorded by the flush.
        self.client.post("/api/vault/unlock", json={"passphrase": "passphrase1"})
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertEqual(entries[self.org["id"]][0]["source_email"], "bob@acme.com")

    def test_unlock_autoscans_and_captures(self):
        self._init()
        self.client.post(f"/api/organizations/{self.org['id']}/domains",
                         json={"domain": "acme.com", "role": "member"})
        self._seed_mail("bob@acme.com")
        self.client.post("/api/vault/lock")
        # No manual scan: unlocking alone auto-scans + captures the queued key.
        self.client.post("/api/vault/unlock", json={"passphrase": "passphrase1"})
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertEqual(entries[self.org["id"]][0]["source_email"], "bob@acme.com")

    def test_reveal_all_route_and_423_when_locked(self):
        self._init()
        add = self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": "P", "secret": "p@ss"}).get_json()
        secrets = self.client.post("/api/vault/reveal-all").get_json()["secrets"]
        self.assertEqual(secrets[add["id"]], "p@ss")
        self.client.post("/api/vault/lock")
        self.assertEqual(self.client.post("/api/vault/reveal-all").status_code, 423)

    def test_search_route_by_value_and_org_name(self):
        self._init()
        self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": "P", "secret": "hunter2"})
        by_value = self.client.post("/api/vault/search",
                                    json={"query": "hunter2"}).get_json()["entries"]
        self.assertIn(self.org["id"], by_value)
        self.assertNotIn("secret", by_value[self.org["id"]][0])   # redacted
        by_org = self.client.post("/api/vault/search",
                                  json={"query": "Acme"}).get_json()["entries"]
        self.assertIn(self.org["id"], by_org)
        self.client.post("/api/vault/lock")
        self.assertEqual(self.client.post("/api/vault/search",
                         json={"query": "x"}).status_code, 423)

    def test_refresh_then_scan_captures_new_mail(self):
        from mailfilter import outlook, routes
        self._init()
        self.client.post(f"/api/organizations/{self.org['id']}/domains",
                         json={"domain": "acme.com", "role": "member"})
        store = self.app.extensions["mail_store"]
        # Stand in for an Outlook fetch+sync that brings in mail with a password.
        orig = outlook.refresh
        outlook.refresh = lambda s: s.add_mails([make_mail(
            id="P", subject="creds", sender_email="bob@acme.com",
            attachments=[], body="Hello,\nPassword:\n Hunter2xZ\nRegards")])
        try:
            routes.refresh_then_scan(
                store, self.app.extensions["password_settings_store"],
                self.app.extensions["vault_store"], self.app.extensions["customer_store"])
        finally:
            outlook.refresh = orig
        entries = self.client.get("/api/vault/entries").get_json()["entries"]
        self.assertEqual(entries[self.org["id"]][0]["source_email"], "bob@acme.com")

    def test_org_listing_carries_nonsecret_vault_index(self):
        self._init()
        self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": "K", "secret": "s3cr3t", "kind": "managed"})
        org = self.client.get("/api/organizations").get_json()["organizations"][0]
        self.assertEqual(org["vault"]["count"], 1)
        self.assertTrue(org["vault"]["has_managed"])
        # The index merged into the org payload must not leak the secret.
        self.assertNotIn("s3cr3t", str(org["vault"]))


if __name__ == "__main__":
    unittest.main()
