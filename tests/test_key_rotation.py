"""Rotating the signing key without logging everybody out.

SEC-09: `TokenService` held exactly one secret, so changing `MYORG_AUTH_SECRET` invalidated
every live token at once. That makes rotation an outage -- and the rotation you most want to
do quickly is the one after a suspected leak, which is exactly when an outage is worst.

`current,previous`: sign with the first, accept either, wait one token lifetime (15 minutes
maximum), drop the old one.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.auth import AuthError, TokenService
from runtime.db import Store

OLD = "old-secret-0123456789abcdef0123456789"
NEW = "new-secret-0123456789abcdef0123456789"


class KeyRotationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "rotate.db")
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.upsert_actor("acme", "operator", "human", "Operator", ["viewer"])

    def test_a_token_from_the_old_key_survives_the_rotation(self) -> None:
        issued = TokenService(self.store, OLD).issue("acme", "operator")
        rotated = TokenService(self.store, f"{NEW},{OLD}")
        self.assertEqual(rotated.verify(issued).actor_id, "operator")

    def test_new_tokens_are_signed_with_the_current_key_only(self) -> None:
        rotated = TokenService(self.store, f"{NEW},{OLD}")
        issued = rotated.issue("acme", "operator")
        TokenService(self.store, NEW).verify(issued)  # the new key alone accepts it
        with self.assertRaises(AuthError):
            TokenService(self.store, OLD).verify(issued)

    def test_dropping_the_old_key_finally_invalidates_its_tokens(self) -> None:
        issued = TokenService(self.store, OLD).issue("acme", "operator")
        with self.assertRaises(AuthError):
            TokenService(self.store, NEW).verify(issued)

    def test_a_key_that_was_never_valid_is_still_refused(self) -> None:
        forged = TokenService(self.store, "attacker-secret-0123456789abcdef01").issue("acme", "operator")
        with self.assertRaises(AuthError):
            TokenService(self.store, f"{NEW},{OLD}").verify(forged)

    def test_a_single_key_behaves_exactly_as_before(self) -> None:
        single = TokenService(self.store, OLD)
        self.assertEqual(single.verify(single.issue("acme", "operator")).actor_id, "operator")

    def test_a_short_key_anywhere_in_the_pair_is_refused(self) -> None:
        for value in (f"{NEW},too-short", f"too-short,{NEW}", "too-short"):
            with self.assertRaises(AuthError):
                TokenService(self.store, value)

    def test_three_keys_are_refused(self) -> None:
        """An overlap window is two keys. Three means somebody forgot to finish a rotation."""
        with self.assertRaises(AuthError):
            TokenService(self.store, f"{NEW},{OLD},{OLD}")


if __name__ == "__main__":
    unittest.main()
